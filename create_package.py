"""Prepares server package from addon repo to upload to server.

Requires Python3.9. (Or at least 3.8+).

This script should be called from cloned addon repo.

It will produce 'package' subdirectory which could be pasted into server
addon directory directly (eg. into `ayon-docker/addons`).

Format of package folder:
ADDON_REPO/package/{addon name}/{addon version}

You can specify `--output_dir` in arguments to change output directory where
package will be created. Existing package directory will be always purged if
already present! This could be used to create package directly in server folder
if available.

Package contains server side files directly,
client side code zipped in `private` subfolder.
"""

import os
import sys
import re
import io
import shutil
import argparse
import platform
import logging
import collections
import zipfile
import tarfile
from typing import Optional, Pattern

import package

ADDON_NAME: str = package.name
ADDON_VERSION: str = package.version
FTRACK_EVENT_HANDLERS_FILENAME = "hibob_event_handlers.tar.gz"

CURRENT_DIR: str = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR: str = os.path.join(CURRENT_DIR, "server")
PUBLIC_DIR: str = os.path.join(CURRENT_DIR, "public")
FTRACK_HANDLERS_DIR: str = os.path.join(CURRENT_DIR, "ftrack_event_handlers")

CLIENT_VERSION_CONTENT = f'''# -*- coding: utf-8 -*-
"""Package declaring HiBob addon version."""
__version__ = "{ADDON_VERSION}"
'''

# Patterns of directories to be skipped for server part of addon
IGNORE_DIR_PATTERNS: list[Pattern] = [
    re.compile(pattern)
    for pattern in {
        # Skip directories starting with '.'
        r"^\.",
        # Skip any pycache folders
        "^__pycache__$"
    }
]

# Patterns of files to be skipped for server part of addon
IGNORE_FILE_PATTERNS: list[Pattern] = [
    re.compile(pattern)
    for pattern in {
        # Skip files starting with '.'
        # NOTE this could be an issue in some cases
        r"^\.",
        # Skip '.pyc' files
        r"\.pyc$"
    }
]


class ZipFileLongPaths(zipfile.ZipFile):
    """Allows longer paths in zip files.

    Regular DOS paths are limited to MAX_PATH (260) characters, including
    the string's terminating NUL character.
    That limit can be exceeded by using an extended-length path that
    starts with the '\\?\' prefix.
    """
    _is_windows = platform.system().lower() == "windows"

    def _extract_member(self, member, tpath, pwd):
        if self._is_windows:
            tpath = os.path.abspath(tpath)
            if tpath.startswith("\\\\"):
                tpath = "\\\\?\\UNC\\" + tpath[2:]
            else:
                tpath = "\\\\?\\" + tpath

        return super()._extract_member(member, tpath, pwd)


def safe_copy_file(src_path: str, dst_path: str):
    """Copy file and make sure destination directory exists.

    Ignore if destination already contains directories from source.

    Args:
        src_path (str): File path that will be copied.
        dst_path (str): Path to destination file.
    """

    if src_path == dst_path:
        return

    dst_dir: str = os.path.dirname(dst_path)
    try:
        os.makedirs(dst_dir)
    except Exception:
        pass

    shutil.copy2(src_path, dst_path)


def _value_match_regexes(value: str, regexes: list[Pattern]) -> bool:
    for regex in regexes:
        if regex.search(value):
            return True
    return False


def find_files_in_subdir(
    src_path: str,
    ignore_file_patterns: Optional[list[Pattern]] = None,
    ignore_dir_patterns: Optional[list[Pattern]] = None
) -> list[tuple[str, str]]:
    if ignore_file_patterns is None:
        ignore_file_patterns: list[Pattern] = IGNORE_FILE_PATTERNS

    if ignore_dir_patterns is None:
        ignore_dir_patterns: list[Pattern] = IGNORE_DIR_PATTERNS
    output: list[tuple[str, str]] = []

    hierarchy_queue: collections.deque[tuple[str, list[str]]] = (
        collections.deque()
    )
    hierarchy_queue.append((src_path, []))
    while hierarchy_queue:
        item = hierarchy_queue.popleft()
        dirpath, parents = item
        for name in os.listdir(dirpath):
            path = os.path.join(dirpath, name)
            if os.path.isfile(path):
                if not _value_match_regexes(name, ignore_file_patterns):
                    items = list(parents)
                    items.append(name)
                    output.append((path, os.path.sep.join(items)))
                continue

            if not _value_match_regexes(name, ignore_dir_patterns):
                items = list(parents)
                items.append(name)
                hierarchy_queue.append((path, items))

    return output


def _get_server_files_mapping() -> list[tuple[str, str]]:
    """Returns mapping of server files to copy to package.

    Returns:
        list[tuple[str, str]]: List of tuples with source and destination
            paths.

    """
    filepaths_to_copy: list[tuple[str, str]] = []
    for path, sub_path in find_files_in_subdir(SERVER_DIR):
        filepaths_to_copy.append(
            (path, os.path.join("server", sub_path))
        )

    for path, sub_path in find_files_in_subdir(PUBLIC_DIR):
        filepaths_to_copy.append(
            (path, os.path.join("public", sub_path))
        )

    filepaths_to_copy.append(
        (os.path.join(CURRENT_DIR, "package.py"), "package.py")
    )
    return filepaths_to_copy


def _prepare_ftrack_tar_content() -> bytes:
    """Prepare tar file with ftrack event handlers.

    Returns:
        str: Tar byte content.

    """
    tar_content = io.BytesIO()
    is_windows = platform.system().lower() == "windows"
    with tarfile.open(fileobj=tar_content, mode="w:gz") as tar:
        for path, sub_path in find_files_in_subdir(FTRACK_HANDLERS_DIR):
            if is_windows:
                sub_path = sub_path.replace("\\", "/")
            tarinfo = tarfile.TarInfo(sub_path)
            tarinfo.mode = int("0777", base=8)
            with open(path, "rb") as f_stream:
                # Go to the end to find out size
                f_stream.seek(0, io.SEEK_END)
                tarinfo.size = f_stream.tell()
                # Go back to start and store content to tar object
                f_stream.seek(0)
                tar.addfile(tarinfo, f_stream)

        version_info = tarfile.TarInfo("lib/hibob_common/version.py")
        version_info.mode = int("0777", base=8)
        version_content = CLIENT_VERSION_CONTENT.encode()
        version_info.size = len(version_content)
        tar.addfile(version_info, io.BytesIO(version_content))
    return tar_content.getvalue()


def copy_server_content(
    addon_output_dir: str,
    log: logging.Logger
):
    """Copies server side folders to 'addon_package_dir'

    Args:
        addon_output_dir (str): package dir in addon repo dir
        log (logging.Logger)
    """

    log.info("Copying server content")

    filepaths_to_copy: list[tuple[str, str]] = _get_server_files_mapping()

    # Copy files
    for src_path, dst_path in filepaths_to_copy:
        safe_copy_file(
            src_path,
            os.path.join(addon_output_dir, dst_path)
        )

    private_dir = os.path.join(addon_output_dir, "private")
    os.makedirs(private_dir, exist_ok=True)
    with open(
        os.path.join(private_dir, FTRACK_EVENT_HANDLERS_FILENAME), "wb"
    ) as stream:
        stream.write(_prepare_ftrack_tar_content())


def create_server_package(output_dir: str, log: logging.Logger):
    """Create server package zip file.

    The zip file can be installed to a server using UI or rest api endpoints.

    Args:
        output_dir (str): Directory path to output zip file.
        log (logging.Logger): Logger object.
    """

    log.info("Creating server package")
    output_path = os.path.join(
        output_dir, f"{ADDON_NAME}-{ADDON_VERSION}.zip"
    )
    os.makedirs(output_dir, exist_ok=True)

    with ZipFileLongPaths(
        output_path, "w", zipfile.ZIP_DEFLATED
    ) as zipf:

        # Copy files
        for src_path, dst_path in _get_server_files_mapping():
            zipf.write(src_path, dst_path)

        ftrack_tar_loc = os.path.join(
            "private", FTRACK_EVENT_HANDLERS_FILENAME
        )
        zipf.writestr(ftrack_tar_loc, _prepare_ftrack_tar_content())

    log.info(f"Output package can be found: {output_path}")


def main(
    output_dir: Optional[str]=None,
    skip_zip: Optional[bool]=False,
):
    log: logging.Logger = logging.getLogger("create_package")

    if not output_dir:
        output_dir = os.path.join(CURRENT_DIR, "package")

    log.info("Start creating package")

    # Skip server zipping
    if not skip_zip:
        create_server_package(output_dir, log)
    else:
        log.info(f"Preparing package for {ADDON_NAME}-{ADDON_VERSION}")
        addon_output_dir: str = os.path.join(
            output_dir, ADDON_NAME, ADDON_VERSION
        )
        if os.path.exists(addon_output_dir):
            log.info(f"Purging {addon_output_dir}")
            shutil.rmtree(addon_output_dir)
        os.makedirs(addon_output_dir, exist_ok=True)
        copy_server_content(addon_output_dir, log)
    log.info("Package creation finished")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-zip",
        dest="skip_zip",
        action="store_true",
        help=(
            "Skip zipping server package and create only"
            " server folder structure."
        )
    )
    parser.add_argument(
        "-o", "--output",
        dest="output_dir",
        default=None,
        help=(
            "Directory path where package will be created"
            " (Will be purged if already exists!)"
        )
    )
    parser.add_argument(
        "--only-client",
        dest="only_client",
        action="store_true",
        help=(
            "Extract only client code. This is useful for development."
            " Requires '-o', '--output' argument to be filled."
        )
    )

    args = parser.parse_args(sys.argv[1:])
    main(args.output_dir, args.skip_zip)
