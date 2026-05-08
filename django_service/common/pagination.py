"""Pagination classes."""
from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class StandardResultsPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "total_pages": self.page.paginator.num_pages,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )


class StandardCursorPagination(CursorPagination):
    """Cursor pagination ordered by ``-created_at``."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200
    ordering = "-created_at"
    cursor_query_param = "cursor"
