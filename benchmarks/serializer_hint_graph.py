"""Experimental prefetch graphs only; never used by production cache code."""

import hashlib
import json

from django.db.models import QuerySet


def model_values(model):
    # Direct attributes avoid descriptors and deferred-field queries.
    assert not model.get_deferred_fields(), "Experiment requires all concrete fields"
    return {
        "model": model._meta.label_lower,
        "database": model._state.db,
        "adding": model._state.adding,
        "fields": {
            field.attname: vars(model)[field.attname] for field in model._meta.concrete_fields
        },
    }


def loaded_values(rows):
    """All concrete Article/Tag values and ordered, already-loaded memberships."""
    values = []
    for row in rows:
        cached = row._prefetched_objects_cache["tags"]
        assert isinstance(cached, QuerySet) and cached._result_cache is not None
        assert cached._prefetch_done, "Tags must be fully prefetched"
        # Exercise the public read API too. no_sql() surrounds every caller.
        tags = list(row.tags.all())
        assert all(a is b for a, b in zip(tags, cached._result_cache, strict=True))
        values.append({"article": model_values(row), "tags": [model_values(tag) for tag in tags]})
    return values


def remove_instance_hints(rows):
    """Remove exactly the per-Article back-edge, failing if ORM layout differs."""
    queries = [row._prefetched_objects_cache["tags"] for row in rows]
    assert len({id(query._hints) for query in queries}) == len(rows), "Shared hints dictionary"
    # Check the whole graph first: a changed Django layout must not silently
    # produce a partially transformed control.
    for row, query in zip(rows, queries, strict=True):
        assert query._hints.get("instance") is row, "Unexpected instance hint"
    for row, query in zip(rows, queries, strict=True):
        attributes, hints = vars(query).copy(), query._hints.copy()
        assert query._hints.pop("instance") is row
        assert vars(query).keys() == attributes.keys()
        assert all(vars(query)[key] is value for key, value in attributes.items())
        assert query._hints.keys() == hints.keys() - {"instance"}
        assert all(query._hints[key] is value for key, value in hints.items() if key != "instance")
    return len(queries)


def paired_payloads(original, control, serializer):
    """Both variants perform this same preparation; return bytes/primitives only."""
    assert original is not control and len(original) == len(control)
    original_objects = set()
    control_objects = set()
    for rows, identities in ((original, original_objects), (control, control_objects)):
        for row in rows:
            query = row._prefetched_objects_cache["tags"]
            identities.update((id(row), id(query), id(query._hints), id(query._result_cache)))
            identities.update(id(tag) for tag in query._result_cache)
    assert original_objects.isdisjoint(control_objects), "Control graph is not independent"
    expected = loaded_values(original)
    assert loaded_values(control) == expected, "Independent graphs differ before removal"
    original_payload = serializer.dumps(original)
    removed = remove_instance_hints(control)
    assert loaded_values(control) == expected, "Hint removal changed loaded data"
    assert loaded_values(original) == expected
    assert serializer.dumps(original) == original_payload, "Original graph was mutated"
    control_payload = serializer.dumps(control)
    for variant, payload in (("original", original_payload), ("without_instance", control_payload)):
        restored = serializer.loads(payload)
        assert loaded_values(restored) == expected, "Round trip changed fields or tags"
        for row in restored:
            hints = row._prefetched_objects_cache["tags"]._hints
            if variant == "original":
                assert hints.get("instance") is row
            else:
                assert "instance" not in hints
        del row, restored
    encoded = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    return {
        "original": original_payload,
        "without_instance": control_payload,
    }, {
        "removed_instance_hints": removed,
        "articles": len(original),
        "tag_instances": sum(len(row["tags"]) for row in expected),
        "semantic_sha256": hashlib.sha256(encoded).hexdigest(),
        "independent_graphs": True,
        "original_unchanged": True,
        "fields_and_loaded_tags_equal": True,
        "roundtrip_equal_without_sql": True,
        "payload_bytes": {
            "original": len(original_payload),
            "without_instance": len(control_payload),
        },
    }
