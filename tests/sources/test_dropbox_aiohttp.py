#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""Tests the Dropbox source class methods"""
import json
import ssl
from unittest import mock
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from aiohttp import StreamReader
from aiohttp.client_exceptions import ClientResponseError, ServerDisconnectedError
from freezegun import freeze_time

from connectors.source import ConfigurableFieldValueError, DataSourceConfiguration
from connectors.sources.dropbox_aiohttp import (
    DropboxDataSource,
    InvalidClientCredentialException,
    InvalidPathException,
    InvalidRefreshTokenException,
)
from connectors.utils import ssl_context
from tests.commons import AsyncIterator
from tests.sources.support import create_source

PATH = "/"
DUMMY_VALUES = "abc#123"

HOST_URLS = {
    "ACCESS_TOKEN_HOST_URL": "https://api.dropboxapi.com/",
    "FILES_FOLDERS_HOST_URL": "https://api.dropboxapi.com/2/",
    "DOWNLOAD_HOST_URL": "https://content.dropboxapi.com/2/",
}
PING = "users/get_current_account"

MOCK_CURRENT_USER = {
    "account_id": "acc_id:1234",
    "name": {
        "given_name": "John",
        "surname": "Wilber",
        "display_name": "John Wilber",
        "abbreviated_name": "JW",
    },
    "email": "john.wilber@abcd.com",
    "country": "US",
}

MOCK_CHECK_PATH = {
    ".tag": "folder",
    "name": "shared",
    "path_lower": "/shared",
    "path_display": "/shared",
    "id": "id:abcd",
    "shared_folder_id": "1234",
}

MOCK_ACCESS_TOKEN = {"access_token": "test2344", "expires_in": "1234555"}
MOCK_ACCESS_TOKEN_FOR_INVALID_REFRESH_TOKEN = {"error": "invalid_grant"}
MOCK_ACCESS_TOKEN_FOR_INVALID_APP_KEY = {
    "error": "invalid_client: Invalid client_id or client_secret"
}

MOCK_FILES_FOLDERS = {
    "entries": [
        {
            ".tag": "folder",
            "name": "dummy folder",
            "path_lower": "/test/dummy folder",
            "path_display": "/test/dummy folder",
            "id": "id:1",
        },
    ],
    "cursor": "abcd#1234",
    "has_more": True,
}

MOCK_FILES_FOLDERS_CONTINUE = {
    "entries": [
        {
            ".tag": "file",
            "name": "index.py",
            "path_lower": "/test/dummy folder/index.py",
            "path_display": "/test/dummy folder/index.py",
            "id": "id:2",
            "client_modified": "2023-01-01T06:06:06Z",
            "server_modified": "2023-01-01T06:06:06Z",
            "size": 200,
            "is_downloadable": True,
        },
    ],
    "cursor": None,
    "has_more": False,
}

EXPECTED_FILES_FOLDERS = [
    {
        "_id": "id:1",
        "type": "Folder",
        "name": "dummy folder",
        "file path": "/test/dummy folder",
        "size": 0,
        "_timestamp": "2023-01-01T06:06:06+00:00",
    },
    {
        "_id": "id:2",
        "type": "File",
        "name": "index.py",
        "file path": "/test/dummy folder/index.py",
        "size": 200,
        "_timestamp": "2023-01-01T06:06:06Z",
    },
]

MOCK_SHARED_FILES = {
    "entries": [
        {
            "access_type": {".tag": "viewer"},
            "name": "index1.py",
            "id": "id:1",
            "time_invited": "2023-01-01T06:06:06Z",
            "preview_url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/index1.py?dl=0",
        },
    ],
    "cursor": "abcd#1234",
}

MOCK_SHARED_FILES_CONTINUE = {
    "entries": [
        {
            "access_type": {".tag": "viewer"},
            "name": "index2.py",
            "id": "id:2",
            "time_invited": "2023-01-01T06:06:06Z",
            "preview_url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/index2.py?dl=0",
        },
    ],
    "cursor": None,
}

MOCK_RECEIVED_FILE_METADATA_1 = {
    "name": "index1.py",
    "id": "id:1",
    "client_modified": "2023-01-01T06:06:06Z",
    "server_modified": "2023-01-01T06:06:06Z",
    "size": 200,
    "preview_type": "text",
    "url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/index1.py?dl=0",
}

MOCK_RECEIVED_FILE_METADATA_2 = {
    "name": "index2.py",
    "id": "id:2",
    "client_modified": "2023-01-01T06:06:06Z",
    "server_modified": "2023-01-01T06:06:06Z",
    "size": 200,
    "preview_type": "text",
    "url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/index2.py?dl=0",
}

EXPECTED_SHARED_FILES = [
    {
        "_id": "id:1",
        "type": "File",
        "name": "index1.py",
        "url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/index1.py?dl=0",
        "size": 200,
        "_timestamp": "2023-01-01T06:06:06Z",
    },
    {
        "_id": "id:2",
        "type": "File",
        "name": "index2.py",
        "url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/index2.py?dl=0",
        "size": 200,
        "_timestamp": "2023-01-01T06:06:06Z",
    },
]

MOCK_ATTACHMENT = {
    "id": "id:1",
    "name": "dummy_file.txt",
    "server_modified": "2023-01-01T06:06:06Z",
    "size": 200,
    "url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/dummy_file.txt?dl=0",
    "is_downloadable": True,
    "path_display": "/test/dummy_file.txt",
}

MOCK_PAPER_FILE = {
    "id": "id:1",
    "name": "dummy_file.paper",
    "server_modified": "2023-01-01T06:06:06Z",
    "size": 200,
    "url": "https://www.dropbox.com/scl/fi/a1xtoxyu0ux73pd7e77ul/dummy_file.paper?dl=0",
    "is_downloadable": False,
    "path_display": "/test/dummy_file.paper",
}

RESPONSE_CONTENT = "# This is the dummy file"
EXPECTED_CONTENT = {
    "_id": "id:1",
    "_timestamp": "2023-01-01T06:06:06Z",
    "_attachment": "IyBUaGlzIGlzIHRoZSBkdW1teSBmaWxl",
}


class mock_ssl:
    """This class contains methods which returns dummy ssl context"""

    def load_verify_locations(self, cadata):
        """This method verify locations"""
        pass


class JSONAsyncMock(AsyncMock):
    def __init__(self, json, status, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._json = json
        self.status = status

    async def json(self):
        return self._json


class StreamReaderAsyncMock(AsyncMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.content = StreamReader


def get_json_mock(mock_response, status):
    async_mock = AsyncMock()
    async_mock.__aenter__ = AsyncMock(
        return_value=JSONAsyncMock(json=mock_response, status=status)
    )
    return async_mock


def get_stream_reader():
    async_mock = AsyncMock()
    async_mock.__aenter__ = AsyncMock(return_value=StreamReaderAsyncMock())
    return async_mock


def side_effect_function(url, headers, data, verify_ssl):
    """Dynamically changing return values for API calls
    Args:
        url, ssl: Params required for get call
    """
    if url == "https://api.dropboxapi.com/2/files/list_folder":
        return AsyncIterator([JSONAsyncMock(MOCK_FILES_FOLDERS, status=200)])
    elif url == "https://api.dropboxapi.com/2/files/list_folder/continue":
        return AsyncIterator([JSONAsyncMock(MOCK_FILES_FOLDERS_CONTINUE, status=200)])


@pytest.mark.asyncio
async def test_configuration():
    """Tests the get configurations method of the Dropbox source class."""
    config = DataSourceConfiguration(
        config=DropboxDataSource.get_default_configuration()
    )
    assert config["path"] == PATH
    assert config["app_key"] == DUMMY_VALUES
    assert config["app_secret"] == DUMMY_VALUES
    assert config["refresh_token"] == DUMMY_VALUES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["path", "app_key", "app_secret", "refresh_token"],
)
async def test_validate_configuration_with_empty_fields_then_raise_exception(field):
    source = create_source(DropboxDataSource)
    source.dropbox_client.configuration.set_field(name=field, value="")

    with pytest.raises(ConfigurableFieldValueError):
        await source.validate_config()


@pytest.mark.asyncio
async def test_validate_configuration_with_valid_path():
    source = create_source(DropboxDataSource)
    source.dropbox_client.configuration.set_field(name="path", value="/shared")

    with patch.object(
        aiohttp.ClientSession,
        "post",
        return_value=JSONAsyncMock(json=MOCK_CHECK_PATH, status=200),
    ):
        await source.validate_config()


@pytest.mark.asyncio
@mock.patch("connectors.utils.apply_retry_strategy")
async def test_validate_configuration_with_invalid_path_then_raise_exception(
    mock_apply_retry_strategy,
):
    source = create_source(DropboxDataSource)
    mock_apply_retry_strategy.return_value = mock.Mock()
    source.dropbox_client.path = "/abc"

    with patch.object(
        aiohttp.ClientSession,
        "post",
        side_effect=ClientResponseError(
            status=409,
            request_info=aiohttp.RequestInfo(
                real_url="", method=None, headers=None, url=""
            ),
            history=None,
        ),
    ):
        with pytest.raises(
            InvalidPathException, match="Configured Path: /abc is invalid"
        ):
            await source.validate_config()


@pytest.mark.asyncio
async def test_set_access_token():
    source = create_source(DropboxDataSource)

    with patch.object(
        aiohttp.ClientSession,
        "post",
        return_value=get_json_mock(mock_response=MOCK_ACCESS_TOKEN, status=200),
    ):
        await source.dropbox_client._set_access_token()
        assert source.dropbox_client.access_token == "test2344"


@pytest.mark.asyncio
@mock.patch("connectors.utils.apply_retry_strategy")
async def test_set_access_token_with_incorrect_app_key_then_raise_exception(
    mock_apply_retry_strategy,
):
    source = create_source(DropboxDataSource)
    mock_apply_retry_strategy.return_value = mock.Mock()

    with patch.object(
        aiohttp.ClientSession,
        "post",
        return_value=get_json_mock(
            mock_response=MOCK_ACCESS_TOKEN_FOR_INVALID_APP_KEY, status=400
        ),
    ):
        with pytest.raises(
            InvalidClientCredentialException,
            match="Configured App Key or App Secret is invalid.",
        ):
            await source.dropbox_client._set_access_token()


@pytest.mark.asyncio
@mock.patch("connectors.utils.apply_retry_strategy")
async def test_set_access_token_with_incorrect_refresh_token_then_raise_exception(
    mock_apply_retry_strategy,
):
    source = create_source(DropboxDataSource)
    mock_apply_retry_strategy.return_value = mock.Mock()

    with patch.object(
        aiohttp.ClientSession,
        "post",
        return_value=get_json_mock(
            mock_response=MOCK_ACCESS_TOKEN_FOR_INVALID_REFRESH_TOKEN, status=400
        ),
    ):
        with pytest.raises(
            InvalidRefreshTokenException, match="Configured Refresh Token is invalid."
        ):
            await source.dropbox_client._set_access_token()


def test_tweak_bulk_options():
    source = create_source(DropboxDataSource)

    source.concurrent_downloads = 10
    options = {"concurrent_downloads": 5}

    source.tweak_bulk_options(options)
    assert options["concurrent_downloads"] == 10


@pytest.mark.asyncio
async def test_close_with_client_session():
    source = create_source(DropboxDataSource)
    _ = source.dropbox_client._get_session

    await source.close()
    assert hasattr(source.dropbox_client.__dict__, "_get_session") is False


@pytest.mark.asyncio
async def test_ping():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()
    with patch.object(
        aiohttp.ClientSession,
        "post",
        return_value=get_json_mock(MOCK_CURRENT_USER, 200),
    ):
        await source.ping()


@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_ping_with_ssl(
    mock_post,
):
    mock_post.return_value.__aenter__.return_value.status = 200
    source = create_source(DropboxDataSource)

    source.dropbox_client.ssl_enabled = True
    source.dropbox_client.certificate = (
        "-----BEGIN CERTIFICATE----- Certificate -----END CERTIFICATE-----"
    )

    with patch.object(ssl, "create_default_context", return_value=mock_ssl()):
        source.dropbox_client.ssl_ctx = ssl_context(
            certificate=source.dropbox_client.certificate
        )
        await source.ping()


@pytest.mark.asyncio
@patch("connectors.sources.dropbox_aiohttp.RETRY_INTERVAL", 0)
async def test_api_call_negative():
    source = create_source(DropboxDataSource)
    source.dropbox_client.retry_count = 4
    source.dropbox_client._set_access_token = AsyncMock()

    with patch.object(
        aiohttp.ClientSession, "post", side_effect=Exception("Something went wrong")
    ):
        with pytest.raises(Exception):
            await anext(
                source.dropbox_client.api_call(
                    base_url=HOST_URLS["FILES_FOLDERS_HOST_URL"],
                    url_name=PING,
                    data=json.dumps(None),
                )
            )

    with patch.object(
        aiohttp.ClientSession, "post", side_effect=ServerDisconnectedError()
    ):
        with pytest.raises(Exception):
            await anext(
                source.dropbox_client.api_call(
                    base_url=HOST_URLS["FILES_FOLDERS_HOST_URL"],
                    url_name=PING,
                    data=json.dumps(None),
                )
            )


@pytest.mark.asyncio
async def test_api_call():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with patch.object(
        aiohttp.ClientSession,
        "post",
        return_value=get_json_mock(MOCK_CURRENT_USER, 200),
    ):
        EXPECTED_RESPONSE = {
            "account_id": "acc_id:1234",
            "name": {
                "given_name": "John",
                "surname": "Wilber",
                "display_name": "John Wilber",
                "abbreviated_name": "JW",
            },
            "email": "john.wilber@abcd.com",
            "country": "US",
        }
        response = await anext(
            source.dropbox_client.api_call(
                base_url=HOST_URLS["FILES_FOLDERS_HOST_URL"],
                url_name=PING,
                data=json.dumps(None),
            )
        )
        actual_response = await response.json()
        assert actual_response == EXPECTED_RESPONSE


@pytest.mark.asyncio
async def test_set_access_token_when_token_expires_at_is_str():
    source = create_source(DropboxDataSource)
    source.dropbox_client.token_expiration_time = "2023-02-10T09:02:23.629821"
    mock_token = {"access_token": "test2344", "expires_in": "1234555"}
    async_response_token = get_json_mock(mock_token, 200)

    with patch.object(aiohttp.ClientSession, "post", return_value=async_response_token):
        actual_response = await source.dropbox_client._set_access_token()
        assert actual_response is None


@pytest.fixture
def patch_default_wait_multiplier():
    with mock.patch("connectors.sources.dropbox.RETRY_INTERVAL", 0):
        yield


@pytest.mark.asyncio
@mock.patch("connectors.utils.apply_retry_strategy")
async def test_api_call_when_token_is_expired(
    mock_apply_retry_strategy,
):
    source = create_source(DropboxDataSource)
    mock_apply_retry_strategy.return_value = mock.Mock()

    with patch.object(
        aiohttp.ClientSession,
        "post",
        side_effect=ClientResponseError(
            status=401,
            request_info=aiohttp.RequestInfo(
                real_url="", method=None, headers=None, url=""
            ),
            history=None,
            message="Unauthorized",
        ),
    ):
        with pytest.raises(ClientResponseError):
            await anext(
                source.dropbox_client.api_call(
                    base_url=HOST_URLS["FILES_FOLDERS_HOST_URL"],
                    url_name=PING,
                    data=json.dumps(None),
                )
            )
        await source.close()


@pytest.mark.asyncio
async def test_api_call_when_status_429_exception():
    source = create_source(DropboxDataSource)
    source.dropbox_client.retry_count = 0

    source.dropbox_client._set_access_token = AsyncMock()

    with patch.object(
        aiohttp.ClientSession,
        "post",
        side_effect=ClientResponseError(
            status=429,
            headers={"Retry-After": 0},
            request_info=aiohttp.RequestInfo(
                real_url="", method=None, headers=None, url=""
            ),
            history=(),
        ),
    ):
        _ = source.dropbox_client._get_session
        with pytest.raises(ClientResponseError):
            await anext(
                source.dropbox_client.api_call(
                    base_url=HOST_URLS["FILES_FOLDERS_HOST_URL"],
                    url_name=PING,
                    data=json.dumps(None),
                )
            )
        await source.close()


@pytest.mark.asyncio
@patch("connectors.sources.dropbox_aiohttp.DEFAULT_RETRY_AFTER", 0)
async def test_api_call_when_status_429_exception_without_retry_after_header():
    source = create_source(DropboxDataSource)
    source.dropbox_client.retry_count = 0

    source.dropbox_client._set_access_token = AsyncMock()

    with patch.object(
        aiohttp.ClientSession,
        "post",
        side_effect=ClientResponseError(
            status=429,
            headers={},
            request_info=aiohttp.RequestInfo(
                real_url="", method=None, headers=None, url=""
            ),
            history=(),
        ),
    ):
        _ = source.dropbox_client._get_session
        with pytest.raises(ClientResponseError):
            await anext(
                source.dropbox_client.api_call(
                    base_url=HOST_URLS["FILES_FOLDERS_HOST_URL"],
                    url_name=PING,
                    data=json.dumps(None),
                )
            )
        await source.close()


@pytest.mark.asyncio
async def test_get_content_when_is_downloadable_is_true():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with mock.patch("aiohttp.ClientSession.post", return_value=get_stream_reader()):
        with mock.patch(
            "aiohttp.StreamReader.iter_chunked",
            return_value=AsyncIterator([bytes(RESPONSE_CONTENT, "utf-8")]),
        ):
            response = await source.get_content(
                attachment=MOCK_ATTACHMENT,
                doit=True,
            )
            assert response == EXPECTED_CONTENT


@pytest.mark.asyncio
async def test_get_content_when_is_shared_is_true():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with mock.patch("aiohttp.ClientSession.post", return_value=get_stream_reader()):
        with mock.patch(
            "aiohttp.StreamReader.iter_chunked",
            return_value=AsyncIterator([bytes(RESPONSE_CONTENT, "utf-8")]),
        ):
            response = await source.get_content(
                attachment=MOCK_ATTACHMENT,
                is_shared=True,
                doit=True,
            )
            assert response == EXPECTED_CONTENT


@pytest.mark.asyncio
async def test_get_content_for_paper_files():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with mock.patch("aiohttp.ClientSession.post", return_value=get_stream_reader()):
        with mock.patch(
            "aiohttp.StreamReader.iter_chunked",
            return_value=AsyncIterator([bytes(RESPONSE_CONTENT, "utf-8")]),
        ):
            response = await source.get_content(
                attachment=MOCK_PAPER_FILE,
                doit=True,
            )
            assert response == EXPECTED_CONTENT


@pytest.mark.asyncio
async def test_get_content_when_no_condition_satisfied_then_skip():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with mock.patch("aiohttp.ClientSession.post", return_value=get_stream_reader()):
        with mock.patch(
            "aiohttp.StreamReader.iter_chunked",
            return_value=AsyncIterator([bytes(RESPONSE_CONTENT, "utf-8")]),
        ):
            SKIPPED_ATTACHMENT = MOCK_ATTACHMENT.copy()
            SKIPPED_ATTACHMENT["is_downloadable"] = False
            response = await source.get_content(
                attachment=SKIPPED_ATTACHMENT,
                doit=True,
            )
            assert response is None


@pytest.mark.asyncio
async def test_get_content_when_empty_extension_then_skip():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with mock.patch("aiohttp.ClientSession.post", return_value=get_stream_reader()):
        with mock.patch(
            "aiohttp.StreamReader.iter_chunked",
            return_value=AsyncIterator([bytes(RESPONSE_CONTENT, "utf-8")]),
        ):
            SKIPPED_ATTACHMENT = MOCK_ATTACHMENT.copy()
            SKIPPED_ATTACHMENT["name"] = "dummy_file"
            response = await source.get_content(
                attachment=SKIPPED_ATTACHMENT,
                doit=True,
            )
            assert response is None


@pytest.mark.asyncio
async def test_get_content_when_size_is_large_then_skip():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with mock.patch("aiohttp.ClientSession.post", return_value=get_stream_reader()):
        with mock.patch(
            "aiohttp.StreamReader.iter_chunked",
            return_value=AsyncIterator([bytes(RESPONSE_CONTENT, "utf-8")]),
        ):
            SKIPPED_ATTACHMENT = MOCK_ATTACHMENT.copy()
            SKIPPED_ATTACHMENT["size"] = 23000000
            response = await source.get_content(
                attachment=SKIPPED_ATTACHMENT,
                doit=True,
            )
            assert response is None


@pytest.mark.asyncio
async def test_get_content_when_extension_is_unsupported_then_skip():
    source = create_source(DropboxDataSource)
    source.dropbox_client._set_access_token = AsyncMock()

    with mock.patch("aiohttp.ClientSession.post", return_value=get_stream_reader()):
        with mock.patch(
            "aiohttp.StreamReader.iter_chunked",
            return_value=AsyncIterator([bytes(RESPONSE_CONTENT, "utf-8")]),
        ):
            SKIPPED_ATTACHMENT = MOCK_ATTACHMENT.copy()
            SKIPPED_ATTACHMENT["name"] = "dummy_file.xyz"
            response = await source.get_content(
                attachment=SKIPPED_ATTACHMENT,
                doit=True,
            )
            assert response is None


@pytest.mark.asyncio
@freeze_time("2023-01-01T06:06:06")
async def test_fetch_files_folders():
    source = create_source(DropboxDataSource)
    source.dropbox_client.path = "/"

    actual_response = []
    with patch.object(
        source.dropbox_client,
        "api_call",
        side_effect=[
            AsyncIterator([JSONAsyncMock(MOCK_FILES_FOLDERS, status=200)]),
            AsyncIterator([JSONAsyncMock(MOCK_FILES_FOLDERS_CONTINUE, status=200)]),
        ],
    ):
        async for document, _ in source._fetch_files_folders("/"):
            actual_response.append(document)

    assert actual_response == EXPECTED_FILES_FOLDERS


@pytest.mark.asyncio
@freeze_time("2023-01-01T06:06:06")
async def test_fetch_shared_files():
    source = create_source(DropboxDataSource)
    source.dropbox_client.path = "/"

    actual_response = []
    with patch.object(
        source.dropbox_client,
        "api_call",
        side_effect=[
            AsyncIterator([JSONAsyncMock(MOCK_SHARED_FILES, status=200)]),
            AsyncIterator([JSONAsyncMock(MOCK_RECEIVED_FILE_METADATA_1, status=200)]),
            AsyncIterator([JSONAsyncMock(MOCK_SHARED_FILES_CONTINUE, status=200)]),
            AsyncIterator([JSONAsyncMock(MOCK_RECEIVED_FILE_METADATA_2, status=200)]),
        ],
    ):
        async for document, _ in source._fetch_shared_files():
            actual_response.append(document)

    assert actual_response == EXPECTED_SHARED_FILES


@pytest.mark.asyncio
@freeze_time("2023-01-01T06:06:06")
@patch.object(
    DropboxDataSource,
    "_fetch_files_folders",
    side_effect=AsyncIterator(
        [
            (EXPECTED_FILES_FOLDERS[0], "files-folders"),
            (EXPECTED_FILES_FOLDERS[1], "files-folders"),
        ],
    ),
)
@patch.object(
    DropboxDataSource,
    "_fetch_shared_files",
    return_value=AsyncIterator(
        [
            (EXPECTED_SHARED_FILES[0], "shared_files"),
            (EXPECTED_SHARED_FILES[1], "shared_files"),
        ],
    ),
)
async def test_get_docs(files_folders_patch, shared_files_patch):
    source = create_source(DropboxDataSource)
    expected_responses = [*EXPECTED_FILES_FOLDERS, *EXPECTED_SHARED_FILES]
    source.get_content = Mock(return_value=EXPECTED_CONTENT)

    documents = []
    async for item, _ in source.get_docs():
        documents.append(item)

    assert documents == expected_responses
