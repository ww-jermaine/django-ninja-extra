import inspect
import time
import warnings
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Iterator,
    List,
    Optional,
    Sequence,
    Type,
    Union,
    cast,
)

from django.http import HttpRequest
from django.http.response import HttpResponse, HttpResponseBase
from django.utils.encoding import force_str
from ninja.constants import NOT_SET
from ninja.operation import (
    AsyncOperation as NinjaAsyncOperation,
    Operation as NinjaOperation,
    PathView as NinjaPathView,
)
from ninja.signature import is_async
from ninja.types import TCallable
from ninja.utils import check_csrf

from ninja_extra.compatible import asynccontextmanager
from ninja_extra.exceptions import APIException
from ninja_extra.helper import get_function_name
from ninja_extra.logger import request_logger
from ninja_extra.signals import route_context_finished, route_context_started
from ninja_extra.types import PermissionType

from .controllers.route.context import RouteContext, get_route_execution_context
from .details import ViewSignature

if TYPE_CHECKING:  # pragma: no cover
    from .controllers.route.route_functions import RouteFunction


class Operation(NinjaOperation):
    def __init__(
        self,
        path: str,
        methods: List[str],
        view_func: Callable,
        *,
        url_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.is_coroutine = is_async(view_func)
        self.url_name = url_name
        super().__init__(path, methods, view_func, **kwargs)
        self.signature = ViewSignature(self.path, self.view_func)

    def _set_auth(
        self, auth: Optional[Union[Sequence[Callable], Callable, object]]
    ) -> None:
        if auth is not None and auth is not NOT_SET:
            self.auth_callbacks = isinstance(auth, Sequence) and auth or [auth]
            for callback in self.auth_callbacks:
                _call_back = (
                    callback if inspect.isfunction(callback) else callback.__call__  # type: ignore
                )

                if not getattr(callback, "is_coroutine", None):
                    setattr(callback, "is_coroutine", is_async(_call_back))

                if is_async(_call_back) and not self.is_coroutine:
                    raise Exception(
                        f"Could apply auth=`{get_function_name(callback)}` "
                        f"to view_func=`{get_function_name(self.view_func)}`.\n"
                        f"N:B - {get_function_name(callback)} can only be used on Asynchronous view functions"
                    )

    def _log_action(
        self,
        logger: Callable[..., Any],
        request: HttpRequest,
        duration: Optional[float] = None,
        ex: Optional[Exception] = None,
        **kwargs: Any,
    ) -> None:
        try:
            msg = (
                f'"{request.method.upper() if request.method else "METHOD NOT FOUND"} - '
                f'{self.view_func.__name__} {request.path}" '
                f"{duration if duration else ''}"
            )
            if hasattr(self.view_func, "get_route_function"):
                route_function: "RouteFunction" = (
                    self.view_func.get_route_function()  # type:ignore
                )
                api_controller = route_function.get_api_controller()

                msg = (
                    f'"{request.method.upper() if request.method else "METHOD NOT FOUND"} - '
                    f'{api_controller.controller_class.__name__}[{self.view_func.__name__}] {request.path}" '
                    f"{duration if duration else ''}"
                )
            if ex:
                msg += (
                    f"{ex.status_code}"
                    if isinstance(ex, APIException)
                    else f"{force_str(ex.args)}"
                )

            logger(msg, **kwargs)
        except (Exception,) as log_ex:
            request_logger.debug(log_ex)

    def get_execution_context(
        self,
        request: HttpRequest,
        temporal_response: HttpResponse,
        *args: Any,
        **kwargs: Any,
    ) -> RouteContext:
        permission_classes: PermissionType = []
        if hasattr(self.view_func, "get_route_function"):
            route_function: "RouteFunction" = (
                self.view_func.get_route_function()  # type:ignore
            )

            _api_controller = route_function.get_api_controller()
            permission_classes = (
                route_function.route.permissions or _api_controller.permission_classes
            )

        return get_route_execution_context(
            request,
            temporal_response,
            permission_classes,
            *args,
            **kwargs,
        )

    @contextmanager
    def _prep_run(
        self, request: HttpRequest, temporal_response: HttpResponse, **kw: Any
    ) -> Iterator[RouteContext]:
        try:
            start_time = time.time()
            context = self.get_execution_context(
                request, temporal_response=temporal_response, **kw
            )
            # send route_context_started signal
            route_context_started.send(RouteContext, route_context=context)

            yield context
            self._log_action(
                request_logger.info,
                request=request,
                duration=time.time() - start_time,
                extra=dict(request=request),
                exc_info=None,
            )
        except Exception as e:
            self._log_action(
                request_logger.error,
                request=request,
                ex=e,
                extra=dict(request=request),
                exc_info=None,
            )
            raise e
        finally:
            # send route_context_finished signal
            route_context_finished.send(RouteContext, route_context=None)

    def run(self, request: HttpRequest, **kw: Any) -> HttpResponseBase:
        error = self._run_checks(request)
        if error:
            return error
        try:
            temporal_response = self.api.create_temporal_response(request)
            with self._prep_run(
                request, temporal_response=temporal_response, **kw
            ) as ctx:
                values = self._get_values(request, kw, temporal_response)
                ctx.kwargs.update(values)
                result = self.view_func(request, **values)
                _processed_results = self._result_to_response(
                    request, result, temporal_response
                )
                return _processed_results
        except Exception as e:
            if isinstance(e, TypeError) and "required positional argument" in str(
                e
            ):  # pragma: no cover
                msg = "Did you fail to use functools.wraps() in a decorator?"
                msg = f"{e.args[0]}: {msg}" if e.args else msg
                e.args = (msg,) + e.args[1:]
            return self.api.on_exception(request, e)


class ControllerOperation(Operation):  # pragma: no cover
    def _log_action(
        self,
        logger: Callable[..., Any],
        request: HttpRequest,
        duration: Optional[float] = None,
        ex: Optional[Exception] = None,
        **kwargs: Any,
    ) -> None:
        try:
            msg = (
                f'"{request.method.upper() if request.method else "METHOD NOT FOUND"} - '
                f'{self.view_func.__name__} {request.path}" '
                f"{duration if duration else ''}"
            )
            if hasattr(self.view_func, "get_route_function"):
                route_function: "RouteFunction" = (
                    self.view_func.get_route_function()  # type:ignore
                )
                api_controller = route_function.get_api_controller()

                msg = (
                    f'"{request.method.upper() if request.method else "METHOD NOT FOUND"} - '
                    f'{api_controller.controller_class.__name__}[{self.view_func.__name__}] {request.path}" '
                    f"{duration if duration else ''}"
                )
            if ex:
                msg += (
                    f"{ex.status_code}"
                    if isinstance(ex, APIException)
                    else f"{force_str(ex.args)}"
                )

            logger(msg, **kwargs)
        except (Exception,) as log_ex:
            request_logger.debug(log_ex)

    def get_execution_context(
        self,
        request: HttpRequest,
        temporal_response: HttpResponse,
        *args: Any,
        **kwargs: Any,
    ) -> RouteContext:
        route_function: "RouteFunction" = (
            self.view_func.get_route_function()  # type:ignore
        )

        if not route_function:
            raise Exception("Route Function is missing")

        return route_function.get_route_execution_context(
            request, temporal_response=temporal_response, *args, **kwargs
        )

    @contextmanager
    def _prep_run(
        self, request: HttpRequest, temporal_response: HttpResponse, **kw: Any
    ) -> Iterator[RouteContext]:
        try:
            start_time = time.time()
            context = self.get_execution_context(
                request, temporal_response=temporal_response, **kw
            )
            # send route_context_started signal
            route_context_started.send(RouteContext, route_context=context)

            yield context
            self._log_action(
                request_logger.info,
                request=request,
                duration=time.time() - start_time,
                extra=dict(request=request),
                exc_info=None,
            )
        except Exception as e:
            self._log_action(
                request_logger.error,
                request=request,
                ex=e,
                extra=dict(request=request),
                exc_info=None,
            )
            raise e
        finally:
            # send route_context_finished signal
            route_context_finished.send(RouteContext, route_context=None)

    def run(self, request: HttpRequest, **kw: Any) -> HttpResponseBase:
        error = self._run_checks(request)
        if error:
            return error
        try:
            temporal_response = self.api.create_temporal_response(request)
            with self._prep_run(
                request, temporal_response=temporal_response, **kw
            ) as ctx:
                values = self._get_values(request, kw, temporal_response)
                ctx.kwargs = values
                result = self.view_func(context=ctx, **values)
                _processed_results = self._result_to_response(
                    request, result, temporal_response
                )
                return _processed_results
        except Exception as e:
            if isinstance(e, TypeError) and "required positional argument" in str(e):
                msg = "Did you fail to use functools.wraps() in a decorator?"
                msg = f"{e.args[0]}: {msg}" if e.args else msg
                e.args = (msg,) + e.args[1:]
            return self.api.on_exception(request, e)


class AsyncOperation(Operation, NinjaAsyncOperation):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from asgiref.sync import sync_to_async

        self._get_values = cast(Callable, sync_to_async(super()._get_values))  # type: ignore
        self._result_to_response = cast(  # type: ignore
            Callable,
            sync_to_async(super()._result_to_response),
        )

    async def _run_checks(self, request: HttpRequest) -> Optional[HttpResponse]:  # type: ignore
        """Runs security checks for each operation"""
        # auth:
        if self.auth_callbacks:
            error = await self._run_authentication(request)
            if error:
                return error

        # csrf:
        if self.api.csrf:
            error = check_csrf(request, self.view_func)
            if error:
                return error

        return None

    async def _run_authentication(self, request: HttpRequest) -> Optional[HttpResponse]:  # type: ignore
        for callback in self.auth_callbacks:
            try:
                is_coroutine = getattr(callback, "is_coroutine", False)
                if is_coroutine:
                    result = await callback(request)
                else:
                    result = callback(request)
            except Exception as exc:
                return self.api.on_exception(request, exc)

            if result:
                request.auth = result  # type: ignore
                return None
        return self.api.create_response(request, {"detail": "Unauthorized"}, status=401)

    @asynccontextmanager
    async def _prep_run(  # type:ignore
        self, request: HttpRequest, **kw: Any
    ) -> AsyncIterator[RouteContext]:
        try:
            start_time = time.time()
            context = self.get_execution_context(request, **kw)
            # send route_context_started signal
            route_context_started.send(RouteContext, route_context=context)

            yield context
            self._log_action(
                request_logger.info,
                request=request,
                duration=time.time() - start_time,
                extra=dict(request=request),
                exc_info=None,
            )
        except Exception as e:
            self._log_action(
                request_logger.error,
                request=request,
                ex=e,
                extra=dict(request=request),
                exc_info=None,
            )
            raise e
        finally:
            # send route_context_finished signal
            route_context_finished.send(RouteContext, route_context=None)

    async def run(self, request: HttpRequest, **kw: Any) -> HttpResponseBase:  # type: ignore
        error = await self._run_checks(request)
        if error:
            return error
        try:
            temporal_response = self.api.create_temporal_response(request)
            async with self._prep_run(
                request, temporal_response=temporal_response, **kw
            ) as ctx:
                values = await self._get_values(request, kw, temporal_response)  # type: ignore
                ctx.kwargs.update(values)
                result = await self.view_func(request, **values)
                _processed_results = await self._result_to_response(request, result, temporal_response)  # type: ignore
                return cast(HttpResponseBase, _processed_results)
        except Exception as e:
            return self.api.on_exception(request, e)


class AsyncControllerOperation(AsyncOperation, ControllerOperation):  # pragma: no cover
    @asynccontextmanager
    async def _prep_run(  # type:ignore
        self, request: HttpRequest, **kw: Any
    ) -> AsyncIterator[RouteContext]:
        try:
            start_time = time.time()
            context = self.get_execution_context(request, **kw)
            # send route_context_started signal
            route_context_started.send(RouteContext, route_context=context)

            yield context
            self._log_action(
                request_logger.info,
                request=request,
                duration=time.time() - start_time,
                extra=dict(request=request),
                exc_info=None,
            )
        except Exception as e:
            self._log_action(
                request_logger.error,
                request=request,
                ex=e,
                extra=dict(request=request),
                exc_info=None,
            )
            raise e
        finally:
            # send route_context_finished signal
            route_context_finished.send(RouteContext, route_context=None)

    async def run(self, request: HttpRequest, **kw: Any) -> HttpResponseBase:  # type: ignore
        error = await self._run_checks(request)
        if error:
            return error
        try:
            temporal_response = self.api.create_temporal_response(request)
            async with self._prep_run(
                request, temporal_response=temporal_response, **kw
            ) as ctx:
                values = await self._get_values(request, kw, temporal_response)  # type: ignore
                ctx.kwargs = values
                result = await self.view_func(context=ctx, **values)
                _processed_results = await self._result_to_response(request, result, temporal_response)  # type: ignore
                return cast(HttpResponseBase, _processed_results)
        except Exception as e:
            return self.api.on_exception(request, e)


class PathView(NinjaPathView):
    async def _async_view(self, request: HttpRequest, *args, **kwargs) -> HttpResponseBase:  # type: ignore
        return await super(PathView, self)._async_view(request, *args, **kwargs)

    def _sync_view(self, request: HttpRequest, *args, **kwargs) -> HttpResponseBase:  # type: ignore
        return super(PathView, self)._sync_view(request, *args, **kwargs)

    def add_operation(
        self,
        path: str,
        methods: List[str],
        view_func: Callable,
        *,
        auth: Optional[Union[Sequence[Callable], Callable, object]] = NOT_SET,
        response: Any = NOT_SET,
        operation_id: Optional[str] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        deprecated: Optional[bool] = None,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        url_name: Optional[str] = None,
        include_in_schema: bool = True,
    ) -> Operation:
        if url_name:
            self.url_name = url_name
        operation_class = self.get_operation_class(view_func)
        operation = operation_class(
            path,
            methods,
            view_func,
            auth=auth,
            response=response,
            operation_id=operation_id,
            summary=summary,
            description=description,
            tags=tags,
            deprecated=deprecated,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            include_in_schema=include_in_schema,
            url_name=url_name,
        )

        self.operations.append(operation)
        return operation

    def get_operation_class(
        self, view_func: TCallable
    ) -> Type[Union[Operation, AsyncOperation]]:
        operation_class = Operation
        if is_async(view_func):
            self.is_async = True
            operation_class = AsyncOperation
        return operation_class


class ControllerPathView(PathView):  # pragma: no cover
    def get_operation_class(
        self, view_func: TCallable
    ) -> Type[Union[Operation, AsyncOperation]]:
        return super(ControllerPathView, self).get_operation_class(view_func)


__deprecated__ = {
    "ControllerOperation": (ControllerOperation, Operation),
    "AsyncControllerOperation": (AsyncControllerOperation, AsyncOperation),
    "ControllerPathView": (ControllerPathView, PathView),
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name in __deprecated__:
        value = __deprecated__[name]
        warnings.warn(
            f"'{name}' is deprecated. Use '{value[1]}' instead.",
            category=DeprecationWarning,
            stacklevel=3,
        )
        return value[0]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
