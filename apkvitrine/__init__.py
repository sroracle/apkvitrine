# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import collections  # defaultdict
import configparser # ConfigParser
from pathlib import Path

VERSION = "0.1"
BUILDERS = "@builders"
DEFAULT = "@default"

SYSCONFDIR = Path(__file__).parent.parent

def _config_map(s):
    d = {}
    for i in s.strip().splitlines():
        i = i.strip().split(maxsplit=1)
        d[i[0]] = i[1]
    return d

def _config_maplist(s):
    d = collections.defaultdict(list)
    for i in s.strip().splitlines():
        i = i.strip().split()
        d[i[0]].extend(i[1:])
    return d

def _ConfigParser(**kwargs):
    parser = configparser.ConfigParser(
        interpolation=None,
        comment_prefixes=(";",),
        delimiters=("=",),
        inline_comment_prefixes=None,
        empty_lines_in_values=True,
        converters={
            "list": lambda s: s.strip().splitlines(),
            "path": Path,
            "map": _config_map,
            "maplist": _config_maplist,
        },
        default_section=DEFAULT,
        **kwargs,
    )
    parser.BOOLEAN_STATES = {"true": True, "false": False}
    return parser

def config(version=None):
    config = _ConfigParser()
    files = sorted(SYSCONFDIR.glob("*.ini"))
    config.read(files)
    if version:
        return config[version]
    return config
