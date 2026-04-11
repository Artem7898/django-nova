"""
Auto-generated Admin REST API.
Replaces django.contrib.admin's coupled views with decoupled JSON API.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import JsonResponse
from django.views import View

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


class NovaAdminAPI(View):
    """
    Generic API view for NovaModel admin operations.
    """
    model: type[NovaModel]

    def get(self, request, pk: int | None = None) -> JsonResponse:
        if pk:
            try:
                obj = self.model.objects.get(pk=pk)
                return JsonResponse(obj.to_pydantic().model_dump(mode="json"))
            except self.model.DoesNotExist:
                return JsonResponse({"error": "Not found"}, status=404)

        # List endpoint
        qs = self.model.objects.all()
        data = [obj.to_pydantic().model_dump(mode="json") for obj in qs]
        return JsonResponse({"results": data, "count": len(data)})

    def post(self, request) -> JsonResponse:
        import json

        from nova.validation.pydantic_bridge import pydantic_to_model

        if not self.model._nova_config.pydantic_schema:
            return JsonResponse({"error": "No Pydantic schema configured"}, status=500)

        try:
            payload = json.loads(request.body)
            schema = self.model._nova_config.pydantic_schema.model_validate(payload)
            obj = pydantic_to_model(self.model, schema)
            obj.save()
            return JsonResponse(obj.to_pydantic().model_dump(mode="json"), status=201)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)