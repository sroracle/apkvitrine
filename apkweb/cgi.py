#!/usr/bin/env python3
# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import datetime     # datetime, timezone
import http         # HTTPStatus
import os           # environ
import sqlite3      # connect
import sys          # exit, path
import urllib.parse # parse_qs
from pathlib import Path

import cgitb        # enable
cgitb.enable()

import jinja2       # Environment, FileSystemBytecodeCache

SRCDIR = Path(__file__).parent.parent
CACHE = Path("/var/tmp/apkweb")
sys.path.insert(0, str(SRCDIR))
import apkweb.models # build_search, Pkg

ENV = jinja2.Environment(
    loader=jinja2.PackageLoader("apkweb", "templates"),
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
    return sqlite3.connect(str(db))

def response(status, *, headers=None, ctype="text/plain"):
    print("HTTP/1.1", status.value, status.phrase)
    print("Content-type:", ctype + ";", "charset=utf-8")
    if headers:
        [print(*i, sep=": ") for i in headers.items()]
    print()

def ok(ctype="text/html"):
    response(http.HTTPStatus.OK, ctype=ctype)

def error(status, ctype="text/plain"):
    response(status, ctype=ctype)
    print("Error", status.value, "-", status.phrase)

def notfound():
    error(http.HTTPStatus.NOT_FOUND)

def badreq(ctype="text/plain"):
    error(http.HTTPStatus.BAD_REQUEST)

def pkg_paginate(conf, query, db, sql):
    query["limit"] = conf.getint("pagination")
    try:
        query["page"] = int(query.get("page", "1"))
    except ValueError:
        query["page"] = 1
    sql_count = f"SELECT COUNT(*) FROM ({sql})"
    query["offset"] = (query["page"] - 1) * query["limit"]
    query["total"] = db.execute(sql_count, query).fetchone()[0]

    db.row_factory = apkweb.models.Pkg.factory
    sql += " LIMIT :limit OFFSET :offset"
    return db.execute(sql, query).fetchall()

def page_branches(path, query):
    conf = apkweb.config()
    branches = list(conf.sections())

    for i, branch in enumerate(branches):
        if not (SRCDIR / f"{branch}.sqlite").is_file():
            del branches[i]

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
    conf = apkweb.config(branch)

    pkgs = pkg_paginate(conf, query, db, """
        SELECT * FROM packages
        WHERE origin IS NULL
        ORDER BY updated DESC
    """)
    ok()

    versions = {}
    repos = set()
    arches = set()
    for i, pkg in enumerate(pkgs):
        repos.add(pkg.repo)
        arches.update(conf.getmaplist("repos")[pkg.repo])
        pkgs[i] = pkg._replace(updated=format_timestamp(pkg.updated))
        versions[pkg.name] = pkg.get_versions(db)

    response = ENV.get_template("branch.tmpl").render(
        conf=conf,
        branch=branch,
        query=query,
        repos=sorted(repos),
        arches=sorted(arches),
        pkgs=pkgs,
        versions=versions,
    )
    print(response)
    save_cache(path, response)

def page_package(path, query):
    branch, name = path.parts
    db = init_db(branch)
    conf = apkweb.config(branch)

    db.row_factory = apkweb.models.Pkg.factory
    pkg = db.execute("""
        SELECT * FROM packages WHERE name = ?;
    """, (name,)).fetchone()
    if not pkg:
        return notfound()

    pkg = pkg._replace(updated=format_timestamp(pkg.updated))
    pkg = pkg._replace(origin=pkg.get_origin(db))
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

def page_search(path, query):
    branch = path.parts[0]
    db = init_db(branch)
    conf = apkweb.config(branch)

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

    if any([j for i, j in query.items() if i not in ("page", "subpkgs", "cs", "sort")]):
        searched = True
        new_query = query.copy()
        sql = apkweb.models.build_search(new_query)
        pkgs = pkg_paginate(conf, new_query, db, sql)
        query["limit"] = new_query["limit"]
        query["offset"] = new_query["offset"]
        query["total"] = new_query["total"]
        query["page"] = new_query["page"]
    else:
        searched = False
        pkgs = []

    response = ENV.get_template("search.tmpl").render(
        conf=conf,
        branch=branch,
        query=query,
        maints=maints,
        searched=searched,
        pkgs=pkgs,
    )
    print(response)
    if not searched:
        save_cache(path, response)

def page_home(path, query):
    conf = apkweb.config("DEFAULT")
    response(
        http.HTTPStatus.TEMPORARY_REDIRECT,
        headers={"Location": conf["default_version"]},
    )

ROUTES = {
    "-/versions": page_branches,
    "*/-/search": page_search,
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
        print("X-Sendfile:", cache)
        print()
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
