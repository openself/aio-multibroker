"""Unit-тесты для иерархии исключений (§2.2, §2.3, G-005)."""
import pytest
from multidict import CIMultiDict, CIMultiDictProxy

from multibroker.clients.alor.exceptions import (
    AlorRestException,
    BrokerAuthError,
    BrokerBadRequestError,
    BrokerForbiddenError,
    BrokerNotFoundError,
    BrokerRateLimitError,
    BrokerServerError,
)


def _make_headers(**kwargs) -> CIMultiDictProxy:
    return CIMultiDictProxy(CIMultiDict(kwargs))


def _make_client_preprocess():
    """Return a standalone _preprocess_rest_response bound to a throwaway AlorClient-like object."""
    # We import here to avoid heavy __init__ JWT flow.
    from multibroker.clients.alor.AlorClient import AlorClient

    class _Stub:
        _preprocess_rest_response = AlorClient._preprocess_rest_response

    return _Stub()


class TestPreprocessRestResponse:
    """Tests for AlorClient._preprocess_rest_response (§2.3)."""

    def setup_method(self):
        self.stub = _make_client_preprocess()

    def test_200_no_exception(self):
        self.stub._preprocess_rest_response(200, _make_headers(), {"ok": True})

    def test_400_raises_bad_request(self):
        with pytest.raises(BrokerBadRequestError) as exc_info:
            self.stub._preprocess_rest_response(400, _make_headers(), {"message": "bad"})
        assert exc_info.value.status_code == 400

    def test_401_raises_auth_error(self):
        with pytest.raises(BrokerAuthError) as exc_info:
            self.stub._preprocess_rest_response(401, _make_headers(), None)
        assert exc_info.value.status_code == 401

    def test_403_raises_forbidden(self):
        with pytest.raises(BrokerForbiddenError):
            self.stub._preprocess_rest_response(403, _make_headers(), None)

    def test_404_raises_not_found(self):
        with pytest.raises(BrokerNotFoundError):
            self.stub._preprocess_rest_response(404, _make_headers(), None)

    def test_429_raises_rate_limit_with_retry_after(self):
        headers = _make_headers(**{"Retry-After": "2.5"})
        with pytest.raises(BrokerRateLimitError) as exc_info:
            self.stub._preprocess_rest_response(429, headers, None)
        assert exc_info.value.retry_after_sec == 2.5

    def test_429_raises_rate_limit_without_retry_after(self):
        with pytest.raises(BrokerRateLimitError) as exc_info:
            self.stub._preprocess_rest_response(429, _make_headers(), None)
        assert exc_info.value.retry_after_sec is None

    def test_500_raises_server_error(self):
        with pytest.raises(BrokerServerError) as exc_info:
            self.stub._preprocess_rest_response(500, _make_headers(), {"error": "internal"})
        assert exc_info.value.status_code == 500

    def test_502_raises_server_error(self):
        with pytest.raises(BrokerServerError):
            self.stub._preprocess_rest_response(502, _make_headers(), None)

    def test_unknown_4xx_raises_base_rest_exception(self):
        with pytest.raises(AlorRestException) as exc_info:
            self.stub._preprocess_rest_response(418, _make_headers(), None)
        assert exc_info.value.status_code == 418


class TestExceptionHierarchy:
    """Verify the inheritance chain is correct."""

    def test_broker_bad_request_is_alor_rest(self):
        assert issubclass(BrokerBadRequestError, AlorRestException)

    def test_broker_server_error_is_alor_rest(self):
        assert issubclass(BrokerServerError, AlorRestException)

    def test_broker_rate_limit_stores_retry_after(self):
        exc = BrokerRateLimitError(429, None, retry_after_sec=3.0)
        assert exc.retry_after_sec == 3.0
