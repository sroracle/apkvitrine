# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import collections # namedtuple
import datetime    # datetime, timezone

def _insert(table, columns):
    values = ", ".join("?" for i in columns)
    columns = ", ".join(columns)
    return f"INSERT INTO {table} ({columns}) VALUES ({values});"

class DbModel:
    _insert_sql = None

    def insert(self, db):
        return db.execute(self._insert_sql, self)

    @classmethod
    def insertmany(cls, db, rows):
        return db.executemany(cls._insert_sql, rows)

    @classmethod
    def factory(cls, _, row):
        return cls(*row)

_Pkg = collections.namedtuple(
    "Pkg", (
        "id",
        "repo",
        "name",
        "description",
        "url",
        "license",
        "origin",
        "maintainer",
        "revision",
        "size",
        "updated",
    ),
)
class Pkg(_Pkg, DbModel):
    __slots__ = ()
    _table = "packages"
    _insert_sql = _insert(_table, _Pkg._fields)

    @classmethod
    def from_index(cls, pkg, origin):
        return cls(
            None, pkg.repo, pkg.name,
            pkg.description, pkg.url, pkg.license,
            origin, pkg.maintainer, pkg.commit, None, None,
        )

    def get_origin(self, db):
        if not self.origin:
            return None

        old_factory = db.row_factory
        db.row_factory = Pkg.factory

        origin = db.execute("""
            SELECT * FROM packages WHERE id = ?;
        """, (self.origin,)).fetchone()

        db.row_factory = old_factory
        return origin

    def get_subpkgs(self, db):
        if self.origin:
            return None

        old_factory = db.row_factory
        db.row_factory = Pkg.factory

        subpkgs = db.execute("""
            SELECT * FROM packages WHERE origin = ?;
        """, (self.id,)).fetchall()

        db.row_factory = old_factory
        return subpkgs

    def get_versions(self, db):
        old_factory = db.row_factory
        db.row_factory = Version.factory

        versions = db.execute("""
            SELECT
                id, package, arch, IFNULL(version, ""), vrank,
                size, revision, created
            FROM versions WHERE package = ?;
        """, (self.id,)).fetchall()

        db.row_factory = old_factory
        return versions

    def get_deps(self, db, reverse=False):
        old_factory = db.row_factory
        db.row_factory = Archdep.factory
        if reverse:
            join = "rdep"
            where = "dep"
        else:
            join = "dep"
            where = "rdep"

        deps = list(db.execute(f"""
            SELECT NULL, 1, packages.name FROM deps
            INNER JOIN packages ON packages.id = deps.{join}
            WHERE {where} = ?;
        """, (self.id,)).fetchall())

        deps += list(db.execute(f"""
            SELECT archdeps.arch, 1, packages.name FROM archdeps
            INNER JOIN packages ON packages.id = archdeps.{join}
            WHERE {where} = ?;
        """, (self.id,)).fetchall())

        if not reverse:
            deps += list(db.execute("""
                SELECT missingdeps.arch, NULL, missingdeps.dep FROM missingdeps
                INNER JOIN packages ON packages.id = missingdeps.package
                WHERE packages.id = ?;
            """, (self.id,)).fetchall())

        db.row_factory = old_factory
        return deps

    def get_bugs(self, db):
        old_factory = db.row_factory
        db.row_factory = Bug.factory

        bugs = db.execute("""
            SELECT bugs.*
            FROM buglinks INNER JOIN bugs ON bugs.id = buglinks.bug
            WHERE buglinks.package = ?;
        """, (self.id,)).fetchall()

        db.row_factory = old_factory
        return bugs

    def get_merges(self, db):
        old_factory = db.row_factory
        db.row_factory = Merge.factory

        merges = db.execute("""
            SELECT merges.*
            FROM mergelinks INNER JOIN merges ON merges.id = mergelinks.merge
            WHERE mergelinks.package = ?;
        """, (self.id,)).fetchall()

        db.row_factory = old_factory
        return merges

_Version = collections.namedtuple(
    "Version", (
        "id",
        "package",
        "arch",
        "version",
        "vrank",
        "size",
        "revision",
        "created",
    ),
)
class Version(_Version, DbModel):
    __slots__ = ()
    _table = "versions"
    _insert_sql = _insert(_table, _Version._fields)

_Dep = collections.namedtuple(
    "Dep", (
        "rdep",
        "dep",
    ),
)
class Dep(_Dep, DbModel):
    __slots__ = ()
    _table = "deps"
    _insert_sql = _insert(_table, _Dep._fields)

_Archdep = collections.namedtuple(
    "Archdep", (
        "arch",
        "rdep",
        "dep",
    ),
)
class Archdep(_Archdep, DbModel):
    __slots__ = ()
    _table = "archdeps"
    _insert_sql = _insert(_table, _Archdep._fields)

_Missingdep = collections.namedtuple(
    "Missingdep", (
        "package",
        "arch",
        "dep",
    ),
)
class Missingdep(_Missingdep, DbModel):
    __slots__ = ()
    _table = "missingdeps"
    _insert_sql = _insert(_table, _Missingdep._fields)

_Bug = collections.namedtuple(
    "Bug", (
        "id",
        "summary",
        "tags",
        "updated",
    ),
)
class Bug(_Bug, DbModel):
    __slots__ = ()
    _table = "bugs"
    _insert_sql = _insert(_table, _Bug._fields)

_Buglink = collections.namedtuple(
    "Buglink", (
        "bug",
        "package",
    ),
)
class Buglink(_Buglink, DbModel):
    __slots__ = ()
    _table = "buglinks"
    _insert_sql = _insert(_table, _Buglink._fields)

_Merge = collections.namedtuple(
    "Merge", (
        "id",
        "summary",
        "tags",
        "updated",
    ),
)
class Merge(_Merge, DbModel):
    __slots__ = ()
    _table = "merges"
    _insert_sql = _insert(_table, _Merge._fields)

_Mergelink = collections.namedtuple(
    "Mergelink", (
        "merge",
        "package",
    ),
)
class Mergelink(_Mergelink, DbModel):
    __slots__ = ()
    _table = "mergelinks"
    _insert_sql = _insert(_table, _Mergelink._fields)

_FUZZY_COLUMNS = (
    "repo",
    "name",
    "description",
    "url",
    "license",
)

_CROSS_COLUMNS = {
    "deps": ("archdeps", "a1", "rdep"),
    "rdeps": ("archdeps", "a2", "dep"),
    "mdeps": ("missingdeps", None, "package"),
    "bugs": ("buglinks", None, "package"),
    "merges": ("mergelinks", None, "package"),
}

_SORT = {
    "name": "name",
    "updated": "updated",
}

def build_search(query):
    sql = "SELECT DISTINCT packages.* FROM packages"

    vers = query.get("vers")
    if vers:
        sql += " INNER JOIN versions ON versions.package = packages.id"

    for field, (table, alias, col) in _CROSS_COLUMNS.items():
        if not query.get(field):
            continue
        if not alias:
            alias = table
        sql += f" INNER JOIN {table} {alias} ON {alias}.{col} = packages.id"

    constraints = []

    cs = query.get("cs")
    for col in _FUZZY_COLUMNS:
        if not query.get(col):
            continue
        if cs:
            constraints.append(f"packages.{col} GLOB :{col}")
        else:
            query[col] = "*" + query[col].upper() + "*"
            constraints.append(f"UPPER(packages.{col}) GLOB :{col}")

    maint = query.get("maintainer")
    if maint == "None":
        constraints.append("packages.maintainer IS NULL")
    elif maint:
        query["maintainer"] += " <*"
        constraints.append("packages.maintainer GLOB :maintainer")

    if not query.get("subpkgs"):
        constraints.append("packages.origin IS NULL")

    if query.get("dirty"):
        constraints.append("packages.revision GLOB '*-dirty'")

    if constraints:
        sql = f"{sql} WHERE {' AND '.join(constraints)}"

    if vers:
        sql += " GROUP BY packages.name, versions.arch HAVING (MIN(versions.vrank) > 0 OR versions.version IS NULL)"

    sort = _SORT.get(query.get("sort"), "name")
    sql += f" ORDER BY {sort}"

    return sql

def gl_strptime(s):
    # 2020-12-22T23:53:51.993Z
    s, micro = s.split(".", maxsplit=1)
    micro = int(micro.replace("Z", "", 1)) * 1000
    s = s.replace("T", " ", 1)
    s = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return s.replace(
        microsecond=micro,
        tzinfo=datetime.timezone.utc,
    ).timestamp()

class Builder:
    __slots__ = (
        "id",
        "name",
        "tags",
        "online",
        "active",
        "seen",
        "running_job",
        "success_job",
        "fail_job",
    )

    def __init__(self, data):
        self.id = data["id"]
        self.name = data["description"]
        self.tags = data.get("tag_list")
        self.online = data["online"]
        self.active = data["active"]
        self.seen = gl_strptime(data.get("contacted_at"))
        self.running_job = self.success_job = self.fail_job = None

class Job:
    __slots__ = (
        "id",
        "pipeline",
        "status",
        "revision",
        "title",
        "finished",
    )

    def __init__(self, data):
        self.id = data["id"]
        self.status = data["status"]
        self.revision = data["commit"]["id"]
        self.title = data["commit"]["title"]
        self.finished = gl_strptime(data["finished_at"])
