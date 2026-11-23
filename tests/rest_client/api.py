from typing import Annotated, Any

from fastapi import FastAPI, Query, Request

BASE_ITEM_COUNT = 20


def create_app() -> FastAPI:
    app = FastAPI(title="REST client test API")
    items = [{"id": number, "name": f"Item {number}"} for number in range(1, BASE_ITEM_COUNT)]

    @app.get("/object")
    async def get_object() -> dict[str, Any]:
        return items[0]

    @app.get("/list")
    async def get_list() -> list[dict[str, Any]]:
        return items

    @app.post("/echo")
    async def echo(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
        return payload

    @app.get("/paginated/items", name="paginated_items")
    async def paginated_items(
            request: Request,
            page: Annotated[int, Query(ge=1)] = 1,
            page_size: Annotated[int, Query(ge=1, le=100)] = 2,
    ) -> dict[str, Any]:
        start = (page - 1) * page_size
        end = start + page_size
        next_url = None
        if end < len(items):
            next_url = str(
                request.url_for("paginated_items").include_query_params(
                    page=page + 1, page_size=page_size
                )
            )
        return {
            "data": {"items": items[start:end]},
            "pagination": {"next": next_url},
        }

    return app
