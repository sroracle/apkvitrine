# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import datetime     # datetime, timezone
import http         # HTTPStatus
import json         # load
import sqlite3      # connect, OperationalError
import time         # time
import urllib.parse # parse_qs, urlencode
import urllib.request # Request, urlopen
from pathlib import Path

import flup.server.fcgi as flup # WSGIServer
import jinja2 # Environment, FileSystemBytecodeCache, Markup, PackageLoader
              # environmentfilter

import apkvitrine        # BUILDERS, config, DEFAULT
import apkvitrine.models # build_search, Pkg

@jinja2.environmentfilter
def datetime_filter(env, timestamp):
    dt = datetime.datetime.fromtimestamp(
        timestamp, datetime.timezone.utc,
    ).astimezone()
    full = dt.strftime("%c %Z")

    if env.globals["cache"]:
        result = f"<span class='datetime'>{full}</span>"
    else:
        now = datetime.datetime.now(datetime.timezone.utc)
        rel = now - dt
        rel -= datetime.timedelta(microseconds=rel.microseconds)
        rel = str(rel) + " ago"
        result = f"<span class='datetime' title='{full}'>{rel}</span>"
    return jinja2.Markup(result)

def init_db(app, branch):
    if "/" in branch:
        raise ValueError("branch may not contain '/'")
    db = app.data / f"{branch}.sqlite"
    if not db.is_file():
        return None
    try:
        return sqlite3.connect(str(db))
    except sqlite3.OperationalError:
        return None

def pkg_paginate(conf, query, db, sql):
    query["limit"] = conf.getint("web.pagination")
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

    for pkg in pkgs:
        repos.add(pkg.repo)
        versions[pkg.name] = pkg.get_versions(db)
    for repo in repos:
        arches.update(conf.getmaplist("repos")[repo])

    return versions, sorted(repos), sorted(arches)

def gl_runner_info(token, endpoint, api):
    request = urllib.request.Request(
        url=f"{endpoint}/{api}",
        method="GET",
        headers={"PRIVATE-TOKEN": token},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)

def page_builders(app):
    if apkvitrine.BUILDERS not in app.conf:
        return app.notfound()
    bconf = app.conf[apkvitrine.BUILDERS]
    if not bconf.get("gl.token") or not bconf.get("gl.api"):
        return app.notfound()

    token = bconf["gl.token"]
    api = bconf["gl.api"]
    builders = gl_runner_info(token, api, "runners")
    builders = [i["id"] for i in builders]

    for i, builder in enumerate(builders):
        builders[i] = apkvitrine.models.Builder(gl_runner_info(
            token, api, f"../../runners/{builder}",
        ))

        jobs = gl_runner_info(
            token, api,
            f"../../runners/{builder}/jobs?status=running&order_by=id&per_page=1",
        )
        if jobs:
            builders[i].running_job = apkvitrine.models.Job(jobs[0])

        jobs = gl_runner_info(
            token, api,
            f"../../runners/{builder}/jobs?status=success&order_by=id&per_page=1",
        )
        if jobs:
            builders[i].success_job = apkvitrine.models.Job(jobs[0])

        jobs = gl_runner_info(
            token, api,
            f"../../runners/{builder}/jobs?status=failed&order_by=id&per_page=1",
        )
        if jobs:
            builders[i].fail_job = apkvitrine.models.Job(jobs[0])

    app.ok()
    page = app.jinja.get_template("builders.tmpl").render(
        conf=app.conf[apkvitrine.DEFAULT],
        builders=builders,
        cached=time.time() if app.cache else None,
    ).encode("utf-8")
    app.save_cache(page)
    return [page]

def page_branches(app):
    branches = list(app.conf.sections())
    show_builders = apkvitrine.BUILDERS in branches

    for i, branch in enumerate(branches):
        if not (app.data / f"{branch}.sqlite").is_file():
            branches[i] = None

    branches = [i for i in branches if i]
    app.ok()
    page = app.jinja.get_template("branches.tmpl").render(
        conf=app.conf[apkvitrine.DEFAULT],
        branches=branches,
        show_builders=show_builders,
    ).encode("utf-8")
    app.save_cache(page)
    return [page]

def page_branch(app):
    branch = app.path.parts[0]
    db = init_db(app, branch)
    if not db or branch not in app.conf:
        return app.notfound()
    conf = app.conf[branch]

    pkgs = pkg_paginate(conf, app.query, db, """
        SELECT * FROM packages
        WHERE origin IS NULL
        ORDER BY updated DESC
    """)
    app.ok()
    versions, repos, arches = pkg_versions(conf, db, pkgs)

    page = app.jinja.get_template("branch.tmpl").render(
        conf=conf,
        branch=branch,
        query=app.query,
        repos=repos,
        arches=arches,
        pkgs=pkgs,
        versions=versions,
    ).encode("utf-8")
    app.save_cache(page)
    return [page]

def page_package(app):
    branch, name = app.path.parts
    db = init_db(app, branch)
    if not db or branch not in app.conf:
        return app.notfound()
    conf = app.conf[branch]

    db.row_factory = apkvitrine.models.Pkg.factory
    pkg = db.execute("""
        SELECT * FROM packages WHERE name = ?;
    """, (name,)).fetchone()
    if not pkg:
        return app.notfound()

    pkg = pkg._replace(origin=pkg.get_origin(db))
    if pkg.maintainer:
        pkg = pkg._replace(maintainer=pkg.maintainer.split(" <")[0])
    subpkgs = pkg.get_subpkgs(db)

    app.ok()
    page = app.jinja.get_template("package.tmpl").render(
        conf=conf,
        branch=branch,
        versions=pkg.get_versions(db),
        arches=sorted(conf.getmaplist("repos")[pkg.repo]),
        pkg=pkg,
        deps=pkg.get_deps(db),
        rdeps=pkg.get_deps(db, reverse=True),
        subpkgs=subpkgs,
        bugs=pkg.get_bugs(db),
        merges=pkg.get_merges(db),
    ).encode("utf-8")
    app.save_cache(page)
    return [page]

# Don't consider it a complete search if only some combination of the
# following are given in a query.
_BORING_TOGGLES = (
    "simple",
    "page",
    "cs",
    "subpkgs",
    "availability",
    "sort",
    "purge",
)

def page_search(app):
    branch = app.path.parts[0]
    db = init_db(app, branch)
    if not db or branch not in app.conf:
        return app.notfound()
    conf = app.conf[branch]

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

    app.ok()

    if any([j for i, j in app.query.items() if i not in _BORING_TOGGLES]):
        searched = True
        new_query = app.query.copy()
        sql = apkvitrine.models.build_search(new_query)
        pkgs = pkg_paginate(conf, new_query, db, sql)
        app.query["limit"] = new_query["limit"]
        app.query["offset"] = new_query["offset"]
        app.query["total"] = new_query["total"]
        app.query["page"] = new_query["page"]
    else:
        searched = False
        pkgs = []

    if app.query.get("availability"):
        versions, repos, arches = pkg_versions(conf, db, pkgs)
    else:
        versions = {}
        repos = []
        arches = []

    page = app.jinja.get_template("search.tmpl").render(
        conf=conf,
        branch=branch,
        query=app.query,
        maints=maints,
        searched=searched,
        repos=repos,
        arches=arches,
        pkgs=pkgs,
        versions=versions,
    ).encode("utf-8")
    if not searched:
        app.save_cache(page)
    return [page]

def page_home(app):
    return app.redirect(app.conf[apkvitrine.DEFAULT]["cgi.default_version"])

def page_notfound(app):
    return app.notfound()

class APKVitrineApp: # pylint: disable=too-many-instance-attributes
    routes = {
        "-/versions": page_branches,
        "-/builders": page_builders,
        "*/-/search": page_search,
        "*/*/*": page_notfound,
        "*/*": page_package,
        "*": page_branch,
        ".": page_home,
    }

    __slots__ = (
        "_response",
        "base",
        "cache",
        "cacheable",
        "conf",
        "data",
        "env",
        "jinja",
        "path",
        "query",
    )

    def __init__(self):
        self.jinja = jinja2.Environment(
            loader=jinja2.PackageLoader("apkvitrine", "data"),
            autoescape=True,
            trim_blocks=True,
            bytecode_cache=jinja2.FileSystemBytecodeCache(),
            extensions=["jinja2.ext.loopcontrols"],
            line_statement_prefix="#",
            line_comment_prefix="##",
        )
        self.jinja.filters["datetime"] = datetime_filter

        self.conf = apkvitrine.config()

        if self.conf[apkvitrine.DEFAULT].get("cgi.cache"):
            self.cache = Path(self.conf[apkvitrine.DEFAULT]["cgi.cache"])
        else:
            self.cache = None
        self.jinja.globals["cache"] = bool(self.cache)

        self.data = Path(self.conf[apkvitrine.DEFAULT]["cgi.data"])

        self._response = self.env = None
        self.base = self.path = self.query = None

    def get_host(self):
        host = self.env.get("HTTP_HOST", "")
        return f"//{host}/" if host else ""

    def handle(self, env, response):
        self.env = env
        self._response = response

        self.base = self.get_host() + env.get("SCRIPT_NAME", "").lstrip("/")
        self.base = self.base.rstrip("/") + "/"
        self.path = Path(env.get("PATH_INFO", "").lstrip("/"))
        self.query = urllib.parse.parse_qs(env.get("QUERY_STRING", ""))
        self.query = {i: j[-1] for i, j in self.query.items()}
        self.cacheable = False if self.query else True

        self.jinja.globals["base"] = self.base
        # Used for pagination on search pages so that "page=x" isn't repeated
        self.jinja.globals["request"] = self.base + str(self.path) + "?"
        self.jinja.globals["request"] += urllib.parse.urlencode(
            {i: j for i, j in self.query.items() if i != "page"}
        )

        page = self.cached_page()
        if page is not None:
            return page

        return self.generate_page()

    def cache_name(self, path):
        return self.cache / path.parent / (path.name + ".html")

    def save_cache(self, page, path=None):
        if not self.cache or not self.cacheable:
            return
        if not path:
            path = self.path

        cache = self.cache_name(path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(page)

    def cached_page(self):
        if not self.cache:
            return None

        cache = self.cache_name(self.path)
        if cache.exists():
            if not self.query:
                self.ok()
                return [cache.read_bytes()]
            if self.query.get("purge") == "1":
                if (time.time() - cache.stat().st_mtime) > 600:
                    cache.unlink()
                return self.redirect(self.path)

        return None

    def generate_page(self):
        if ".." in self.path.parts:
            return self.badreq()

        for route, handler in self.routes.items():
            if route == ".":
                if route == str(self.path):
                    return handler(self)
            elif self.path.match(route):
                return handler(self)
        return self.notfound()

    def response(self, status, *, ctype=None, headers=None):
        if not headers:
            headers = []
        if ctype or "content-type" not in [i[0].lower() for i in headers]:
            headers.append(("Content-Type", ctype or "text/html; charset=utf-8"))
        self._response(f"{status.value} {status.phrase}", headers)

    def error(self, status, **kwargs):
        self.response(status, ctype="text/plain", **kwargs)
        return [f"Error {status.value} - {status.phrase}"]

    def notfound(self, **kwargs):
        return self.error(http.HTTPStatus.NOT_FOUND, **kwargs)

    def ok(self, **kwargs):
        self.response(http.HTTPStatus.OK, **kwargs)

    def badreq(self, **kwargs):
        return self.error(http.HTTPStatus.BAD_REQUEST, **kwargs)

    def redirect(self, location):
        self.response(
            http.HTTPStatus.TEMPORARY_REDIRECT,
            headers=[("Location", self.base + str(location))],
        )
        return []

def main():
    app = APKVitrineApp()
    flup.WSGIServer(app.handle).run()

if __name__ == "__main__":
    main()
