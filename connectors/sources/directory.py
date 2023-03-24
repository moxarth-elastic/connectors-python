#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""
Demo of a standalone source
"""
import functools
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from connectors.logger import logger
from connectors.source import BaseDataSource
from connectors.utils import TIKA_SUPPORTED_FILETYPES, get_base64_value

DEFAULT_CONTENT_EXTRACTION = True
DEFAULT_DIR = os.environ.get("SYSTEM_DIR", os.path.dirname(__file__))


class DirectoryDataSource(BaseDataSource):
    """Directory"""

    name = "System Directory"
    service_type = "dir"

    def __init__(self, configuration):
        super().__init__(configuration=configuration)
        self.directory = os.path.abspath(self.configuration["directory"])
        self.pattern = self.configuration["pattern"]
        self.enable_content_extraction = self.configuration["enable_content_extraction"]

    @classmethod
    def get_default_configuration(cls):
        return {
            "directory": {
                "value": DEFAULT_DIR,
                "label": "Directory path",
                "type": "str",
            },
            "pattern": {
                "value": "**/*.*",
                "label": "File glob-like pattern",
                "type": "str",
            },
            "enable_content_extraction": {
                "value": DEFAULT_CONTENT_EXTRACTION,
                "label": "Enable content extraction (true/false)",
                "type": "bool",
            },
        }

    async def ping(self):
        return True

    async def changed(self):
        return True

    def get_id(self, path):
        return hashlib.md5(str(path).encode("utf8")).hexdigest()

    async def _download(self, path, timestamp=None, doit=None):
        if not (
            self.enable_content_extraction
            and doit
            and os.path.splitext(path)[-1] in TIKA_SUPPORTED_FILETYPES
        ):
            return

        print(f"Reading {path}")
        with open(file=path, mode="rb") as f:
            return {
                "_id": self.get_id(path),
                "_timestamp": timestamp,
                "_attachment": get_base64_value(f.read()),
            }

    async def get_docs(self, filtering=None):
        logger.debug(f"Reading {self.directory}...")
        root_directory = Path(self.directory)

        for path_object in root_directory.glob(self.pattern):
            if not path_object.is_file():
                continue

            # download coroutine
            download_coro = functools.partial(self._download, str(path_object))

            # get the last modified value of the file
            stat = path_object.stat()
            ts = stat.st_mtime
            ts = datetime.fromtimestamp(ts, tz=timezone.utc)

            # send back as a doc
            doc = {
                "path": str(path_object),
                "last_modified_time": ts,
                "inode_protection_mode": stat.st_mode,
                "inode_number": stat.st_ino,
                "device_inode_reside": stat.st_dev,
                "number_of_links": stat.st_nlink,
                "uid": stat.st_uid,
                "gid": stat.st_gid,
                "ctime": stat.st_ctime,
                "last_access_time": stat.st_atime,
                "size": stat.st_size,
                "_timestamp": ts.isoformat(),
                "_id": self.get_id(path_object),
            }

            yield doc, download_coro
