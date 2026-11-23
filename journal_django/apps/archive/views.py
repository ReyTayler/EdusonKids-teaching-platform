from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsSuperAdmin
from .repository import archive_query
from .serializers import ArchiveQuerySerializer


class ArchiveView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        query = ArchiveQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        rows, counts, archive_counts = archive_query(**{
            'query': query.validated_data['q'],
            'entity_type': query.validated_data['entity_type'],
        })
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response(list(page))
        response.data.update(counts=counts, archive_counts=archive_counts)
        return response
