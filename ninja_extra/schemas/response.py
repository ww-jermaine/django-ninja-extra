import sys
from typing import Any, Generic, List, Optional, Type, TypeVar

from ninja import Schema
from ninja.constants import NOT_SET
from pydantic.generics import GenericModel
from pydantic.main import BaseModel
from pydantic.networks import AnyHttpUrl

from ninja_extra.generic import GenericType

T = TypeVar("T")


class BasePaginatedResponseSchema(Schema):
    count: int
    next: Optional[AnyHttpUrl]
    previous: Optional[AnyHttpUrl]
    results: List[Any]


class BaseNinjaResponseSchema(Schema):
    count: int
    items: List[Any]


class IdSchema(GenericType, generic_base_name="IdSchema"):
    def get_generic_type(self, wrap_type: Any) -> Type[Schema]:  # pragma: no cover
        class _IDSchema(Schema):
            id: wrap_type  # type: ignore

        return _IDSchema


class OkSchema(GenericType, generic_base_name="OkSchema"):
    def get_generic_type(self, wrap_type: Any) -> Type[Schema]:  # pragma: no cover
        class _OKSchema(Schema):
            detail: wrap_type  # type: ignore

        return _OKSchema


class DetailSchema(GenericType, generic_base_name="DetailSchema"):
    def get_generic_type(self, wrap_type: Any) -> Type[Schema]:  # pragma: no cover
        class _DetailSchema(Schema):
            detail: wrap_type  # type: ignore

        return _DetailSchema


class PaginatedResponseSchema(GenericType, generic_base_name="PaginatedResponseSchema"):
    def get_generic_type(
        self, wrap_type: Any
    ) -> Type[BasePaginatedResponseSchema]:  # pragma: no cover
        class ListResponseSchema(BasePaginatedResponseSchema):
            results: List[wrap_type]  # type: ignore

        return ListResponseSchema


class NinjaPaginationResponseSchema(
    GenericType, generic_base_name="NinjaPaginationResponseSchema"
):
    def get_generic_type(
        self, wrap_type: Any
    ) -> Type[BaseNinjaResponseSchema]:  # pragma: no cover
        class ListNinjaResponseSchema(BaseNinjaResponseSchema):
            items: List[wrap_type]  # type: ignore

        return ListNinjaResponseSchema


if sys.version_info >= (3, 7):  # pragma: no cover

    class PaginatedResponseSchema(
        GenericModel, Generic[T], BasePaginatedResponseSchema
    ):
        results: List[T]

    # Pydantic GenericModels has not way of identifying the _orig
    # __generic_model__ is more like a fix for that
    PaginatedResponseSchema.__generic_model__ = PaginatedResponseSchema

    class NinjaPaginationResponseSchema(
        GenericModel, Generic[T], BaseNinjaResponseSchema
    ):
        items: List[T]

    NinjaPaginationResponseSchema.__generic_model__ = NinjaPaginationResponseSchema

    class IdSchema(GenericModel, Generic[T], Schema):
        id: T

    IdSchema.__generic_model__ = IdSchema

    class OkSchema(GenericModel, Generic[T], Schema):
        detail: T = "Action was successful"

    OkSchema.__generic_model__ = OkSchema

    class DetailSchema(GenericModel, Generic[T], Schema):
        detail: T

    DetailSchema.__generic_model__ = DetailSchema


class RouteParameter(BaseModel):
    path: str
    methods: List[str]
    auth: Any = NOT_SET
    response: Any = NOT_SET
    operation_id: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    deprecated: Optional[bool] = None
    by_alias: bool = False
    exclude_unset: bool = False
    exclude_defaults: bool = False
    exclude_none: bool = False
    url_name: Optional[str] = None
    include_in_schema: bool = True
