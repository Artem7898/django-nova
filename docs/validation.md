# Validation and serialization

`NovaModel.save()` validates the complete model before calling Django's save
implementation. The stages run in this order:

1. Validate the selected Pydantic schema, when `strict_validation=True`.
2. Validate and convert concrete Django fields, assigning cleaned values back to
   the instance.
3. Run `Model.clean()`.
4. Check uniqueness and model constraints.
5. Save through Django.

An earlier failure stops later stages. Nova reports validation failures as
`NovaValidationError`; database errors remain a separate persistence concern.
Pre-save checks cannot replace database constraints under concurrent writes.

## Strictness and conversion

`NovaConfig(strict_validation=False)` disables only the Pydantic save stage.
Django field validation, conversion, `clean()`, uniqueness, and constraints
still run. It is unrelated to Pydantic's own strict type-coercion configuration.

Calling `to_pydantic()` explicitly still performs schema validation. Successful
Pydantic coercion alone does not rewrite Django fields; the subsequent Django
field stage normalizes values before `clean()`. The [demo](quickstart.md)
exercises a Decimal value supplied as a string.

`save(update_fields=...)` retains full-model validation. `QuerySet.update()`,
`bulk_create()`, and `bulk_update()` bypass model `save()` and its validation.
Directly calling `clean()` also omits the preceding field-conversion stage.

## Generated and explicit schemas

The field compiler resolves supported Django types through their class hierarchy.
Unknown fields use `Any`, which preserves values without a specialized Pydantic
type check. Nullable fields, generated timestamps, primary keys, defaults, and
Decimal digit constraints are handled separately. Callable defaults are retained
as factories rather than evaluated while compiling a schema.

`auto_now` and `auto_now_add` fields are optional before persistence; Django sets
their values during save. An explicit `pydantic_schema` controls its own required
fields and defaults. Nova does not rewrite that schema from Django metadata.

## Serialization boundary

`to_dict()` selects attributes using the configured schema, or a generated scalar
schema. `to_pydantic()` validates that dictionary against the selected schema.

- Undeclared fields are not read into the payload. An explicit schema can omit
  the primary key; generated scalar schemas include it.
- `exclude_from_pydantic` applies to automatically generated schemas. For an
  explicit schema, control its declared fields directly.
- File fields become names/paths. Serializing a model does not read file bytes.
- Django `FileField.max_length` describes the stored name. The current compiler
  does not transfer it as a Pydantic value-length limit; Django validation still
  checks the field on save. File size and content validation are separate rules.
- The default scalar representation omits relations, including M2M, so it can be
  used before the model is saved.

Supported explicit nested foreign-key schemas can read a related object.
That may execute SQL if the relation was not loaded. Do not assume that nested
serialization is safe in an async context without preparing its data. Nested
M2M and reverse-relation payloads are not implied by the scalar API.

See the [validation API](api/validation.md) for signatures.
