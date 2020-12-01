#!/usr/bin/env python3
# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import datetime     # datetime, timezone
import http         # HTTPStatus
import os           # environ
import sqlite3      # connect, OperationalError
import sys          # exit, path
import urllib.parse # parse_qs, urlencode
from pathlib import Path

import cgitb        # enable
cgitb.enable()

import jinja2       # Environment, FileSystemBytecodeCache, PackageLoader

SRCDIR = Path(__file__).parent.parent
CACHE = Path("/var/tmp/apkvitrine")
sys.path.insert(0, str(SRCDIR))
import apkvitrine.models # build_search, Pkg

ENV = jinja2.Environment(
    loader=jinja2.PackageLoader("apkvitrine", "templates"),
    autoescape=True,
    trim_blocks=True,
    bytecode_cache=jinja2.FileSystemBytecodeCache(),
    extensions=["jinja2.ext.loopcontrols"],
    line_statement_prefix="#",
    line_comment_prefix="##",
)

def cache_name(path):
    return CACHE / path.parent / (path.name + ".html")

def save_cache(path, response):
    if CACHE.is_dir():
        cache = cache_name(path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(response)

def format_timestamp(timestamp):
    return datetime.datetime.fromtimestamp(
        timestamp, datetime.timezone.utc,
    ).astimezone().strftime("%c %Z")

def init_db(branch):
    assert "/" not in branch
    db = SRCDIR / f"{branch}.sqlite"
    if not db.is_file():
        notfound()
        return None
    try:
        return sqlite3.connect(str(db))
    except sqlite3.OperationalError:
        error(http.HTTPStatus.INTERNAL_SERVER_ERROR)
        return None

def response(status, *, headers=None, ctype="text/html"):
    print("HTTP/1.1", status.value, status.phrase)
    print("Content-type:", ctype + ";", "charset=utf-8")
    if headers is None:
        headers = {}
    for header in headers.items():
        print(*header, sep=": ")
    print()

def ok(**kwargs):
    response(http.HTTPStatus.OK, **kwargs)

def error(status, **kwargs):
    if not kwargs.get("ctype"):
        kwargs["ctype"] = "text/plain"
    response(status, **kwargs)
    print("Error", status.value, "-", status.phrase)

def notfound(**kwargs):
    error(http.HTTPStatus.NOT_FOUND, **kwargs)

def badreq(**kwargs):
    error(http.HTTPStatus.BAD_REQUEST, **kwargs)

def pkg_paginate(conf, query, db, sql):
    query["limit"] = conf.getint("pagination")
    try:
        query["page"] = int(query.get("page", "1"))
    except ValueError:
        query["page"] = 1
    sql_count = f"SELECT COUNT(*) FROM ({sql})"
    query["offset"] = (query["page"] - 1) * query["limit"]
    query["total"] = db.execute(sql_count, query).fetchone()[0]

    db.row_factory = apkvitrine.models.Pkg.factory
    sql += " LIMIT :limit OFFSET :offset"
    return db.execute(sql, query).fetchall()

def pkg_versions(conf, db, pkgs):
    versions = {}
    repos = set()
    arches = set()

    for i, pkg in enumerate(pkgs):
        repos.add(pkg.repo)
        pkgs[i] = pkg._replace(updated=format_timestamp(pkg.updated))
        versions[pkg.name] = pkg.get_versions(db)
    for repo in repos:
        arches.update(conf.getmaplist("repos")[repo])

    return versions, sorted(repos), sorted(arches)

def page_branches(path, _query):
    conf = apkvitrine.config()
    branches = list(conf.sections())

    for i, branch in enumerate(branches):
        if not (SRCDIR / f"{branch}.sqlite").is_file():
            branches[i] = None

    branches = [i for i in branches if i]
    ok()
    response = ENV.get_template("branches.tmpl").render(
        conf=conf["DEFAULT"],
        branches=branches,
    )
    print(response)
    save_cache(path, response)

def page_branch(path, query):
    branch = path.parts[0]
    db = init_db(branch)
    if not db:
        return
    conf = apkvitrine.config(branch)

    pkgs = pkg_paginate(conf, query, db, """
        SELECT * FROM packages
        WHERE origin IS NULL
        ORDER BY updated DESC
    """)
    ok()
    versions, repos, arches = pkg_versions(conf, db, pkgs)

    response = ENV.get_template("branch.tmpl").render(
        conf=conf,
        branch=branch,
        query=query,
        repos=repos,
        arches=arches,
        pkgs=pkgs,
        versions=versions,
    )
    print(response)
    save_cache(path, response)

def page_package(path, _query):
    branch, name = path.parts
    db = init_db(branch)
    if not db:
        return
    conf = apkvitrine.config(branch)

    db.row_factory = apkvitrine.models.Pkg.factory
    pkg = db.execute("""
        SELECT * FROM packages WHERE name = ?;
    """, (name,)).fetchone()
    if not pkg:
        notfound()
        return

    pkg = pkg._replace(updated=format_timestamp(pkg.updated))
    pkg = pkg._replace(origin=pkg.get_origin(db))
    if pkg.maintainer:
        pkg = pkg._replace(maintainer=pkg.maintainer.split(" <")[0])
    subpkgs = pkg.get_subpkgs(db)
    if pkg.origin:
        startdir = pkg.repo + "/" + pkg.origin.name
    else:
        startdir = pkg.repo + "/" + pkg.name

    ok()
    response = ENV.get_template("package.tmpl").render(
        conf=conf,
        branch=branch,
        versions=pkg.get_versions(db),
        arches=sorted(conf.getmaplist("repos")[pkg.repo]),
        pkg=pkg,
        startdir=startdir,
        deps=pkg.get_deps(db),
        rdeps=pkg.get_deps(db, reverse=True),
        subpkgs=subpkgs,
        bugs=pkg.get_bugs(db),
        merges=pkg.get_merges(db),
    )
    print(response)
    save_cache(path, response)

# Don't consider it a complete search if only some combination of the
# following are given in a query.
_BORING_TOGGLES = (
    "page",
    "cs",
    "subpkgs",
    "availability",
    "sort",
)

def page_search(path, query):
    branch = path.parts[0]
    db = init_db(branch)
    if not db:
        return
    conf = apkvitrine.config(branch)

    maints = set(db.execute("""
        SELECT DISTINCT(maintainer) FROM packages
        ORDER BY maintainer;
    """).fetchall())
    have_none = False
    if (None,) in maints:
        have_none = True
        maints.remove((None,))
    maints = sorted({j.split(" <")[0] for i in maints for j in i})
    if have_none:
        maints.insert(0, "None")

    ok()

    if any([j for i, j in query.items() if i not in _BORING_TOGGLES]):
        searched = True
        new_query = query.copy()
        sql = apkvitrine.models.build_search(new_query)
        pkgs = pkg_paginate(conf, new_query, db, sql)
        query["limit"] = new_query["limit"]
        query["offset"] = new_query["offset"]
        query["total"] = new_query["total"]
        query["page"] = new_query["page"]
    else:
        searched = False
        pkgs = []

    if query.get("availability"):
        versions, repos, arches = pkg_versions(conf, db, pkgs)
    else:
        versions = {}
        repos = []
        arches = []

    response = ENV.get_template("search.tmpl").render(
        conf=conf,
        branch=branch,
        query=query,
        maints=maints,
        searched=searched,
        repos=repos,
        arches=arches,
        pkgs=pkgs,
        versions=versions,
    )
    print(response)
    if not searched:
        save_cache(path, response)

def page_home(_path, _query):
    conf = apkvitrine.config("DEFAULT")
    location = ENV.globals["base"] + "/" + conf["default_version"]
    response(
        http.HTTPStatus.TEMPORARY_REDIRECT,
        headers={"Location": location},
    )

ROUTES = {
    "-/versions": page_branches,
    "*/-/search": page_search,
    "*/*/*": lambda _path, _query: notfound(),
    "*/*": page_package,
    "*": page_branch,
    ".": page_home,
}

if __name__ == "__main__":
    path = Path(os.environ.get("PATH_INFO", "").lstrip("/"))
    query = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
    query = {i: j[-1] for i, j in query.items()}
    ENV.globals["base"] = os.environ.get("SCRIPT_NAME") or ""

    # Used for pagination on search pages so that "page=x" isn't repeated
    ENV.globals["request"] = ENV.globals["base"] + "/" + str(path) + "?"
    ENV.globals["request"] += urllib.parse.urlencode(
        {i: j for i, j in query.items() if i != "page"}
    )

    if ".." in path.parts:
        badreq()
        sys.exit(0)

    cache = cache_name(path)
    if cache.exists() and not query:
        ok(headers={"X-Sendfile": cache})
        sys.exit(0)

    for route, handler in ROUTES.items():
        if route == ".":
            if route == str(path):
                handler(path, query)
                break
        elif path.match(route):
            handler(path, query)
            break
    else:
        notfound()
