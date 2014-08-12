#
# Copyright (c) 2010, 2014, Oracle and/or its affiliates. All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
#

"""
This module contains and abstraction of a MySQL user object.
"""

from collections import namedtuple, defaultdict

import re

from mysql.utilities.exception import UtilError, UtilDBError, FormatError
from mysql.utilities.common.ip_parser import parse_connection, clean_IPv6
from mysql.utilities.common.sql_transform import (is_quoted_with_backticks,
                                                  quote_with_backticks)


def change_user_privileges(server, user_name, user_passwd, host,
                           grant_list=None, revoke_list=None,
                           disable_binlog=False, create_user=False):
    """ Change the privileges of a new or existing user.

    This method GRANT or REVOKE privileges to a new user (creating it) or
    existing user.

    server[in]          MySQL server instances to apply changes
                        (from mysql.utilities.common.server.Server).
    user_name[in]       user name to apply changes.
    user_passwd[in]     user's password.
    host[in]            host name associated to the user account.
    grant_list[in]      List of privileges to GRANT.
    revoke_list[in]     List of privileges to REVOKE.
    disable_binlog[in]  Boolean value to determine if the binary logging
                        will be disabled to perform this operation (and
                        re-enabled at the end). By default: False (do not
                        disable binary logging).
    create_user[in]     Boolean value to determine if the user will be
                        created before changing its privileges. By default:
                        False (do no create user).
    """
    if disable_binlog:
        server.exec_query("SET SQL_LOG_BIN=0")
    if create_user:
        server.exec_query("CREATE USER '{0}'@'{1}' IDENTIFIED BY "
                          "'{2}'".format(user_name, host, user_passwd))
    if grant_list:
        grants_str = ", ".join(grant_list)
        server.exec_query("GRANT {0} ON *.* TO '{1}'@'{2}' IDENTIFIED BY "
                          "'{3}'".format(grants_str, user_name, host,
                                         user_passwd))
    if revoke_list:
        revoke_str = ", ".join(revoke_list)
        server.exec_query("REVOKE {0} ON *.* FROM '{1}'@'{2}'"
                          "".format(revoke_str, user_name, host))
    if disable_binlog:
        server.exec_query("SET SQL_LOG_BIN=1")


def parse_user_host(user_name):
    """Parse user, passwd, host, port from user:passwd@host

    user_name[in]      MySQL user string (user:passwd@host)
    """

    no_ticks = user_name.replace("'", "")
    try:
        conn_values = parse_connection(no_ticks)
    except FormatError:
        raise UtilError("Cannot parse user:pass@host : %s." %
                        no_ticks)
    return (conn_values['user'], conn_values['passwd'], conn_values['host'])


def grant_proxy_ssl_privileges(server, user, passw, at='localhost',
                               privs="ALL PRIVILEGES", grant_opt=True,
                               ssl=True, grant_proxy=True):
    """Grant privileges to an user in a server with GRANT OPTION or/and
    REQUIRE SSL if required.

    server[in]         Server to execute the grant query at.
    user_name[in]      New user name.
    passw[in]          password of the new user.
    at[in]             Used in GRANT "TO '{0}'@'{1}'".format(user, at),
                       (default localhost)
    grant_opt[in]      if True, it will grant with GRANT OPTION (default True).
    ssl[in]            if True, it will set REQUIRE SSL (default True).
    grant_proxy[in]    if True, it will grant GRANT PROXY (default True).

    Note: Raises UtilError on any Error.
    """

    grant = [
        "GRANT", privs,
        "ON *.*",
        "TO '{0}'@'{1}'".format(user, at),
        "IDENTIFIED BY '{0}'".format(passw) if passw else "",
        "REQUIRE SSL" if ssl else "",
        "WITH GRANT OPTION" if grant_opt else ""
    ]

    try:
        server.exec_query(" ".join(grant))
    except UtilDBError as err:
        raise UtilError("Cannot create new user {0} at {1}:{2} reason:"
                        "{3}".format(user, server.host, server.host,
                                     err.errmsg))

    if grant_proxy:
        grant = [
            "GRANT PROXY ON ''@''",
            "TO '{0}'@'{1}'".format(user, at),
            "WITH GRANT OPTION"
        ]
        try:
            server.exec_query(" ".join(grant))
        except UtilDBError as err:
            raise UtilError("Cannot grant proxy to user {0} at {1}:{2} "
                            "reason:{3}".format(user, server.host,
                                                server.host, err.errmsg))


class User(object):
    """
    The User class can be used to clone the user and its grants to another
    user with the following utilities:

        - Parsing user@host:passwd strings
        - Create, Drop user
        - Check to see if user exists
        - Retrieving and printing grants for user
    """

    def __init__(self, server1, user, verbosity=0):
        """Constructor

        server1[in]        Server class
        user[in]           MySQL user credentials string (user@host:passwd)
        verbose[in]        print extra data during operations (optional)
                           default value = False
        """

        self.server1 = server1
        self.user, self.passwd, self.host = parse_user_host(user)
        self.verbosity = verbosity
        self.current_user = None
        self.grant_dict = None
        self.global_grant_dict = None
        self.grant_list = None
        self.global_grant_list = None
        self.query_options = {
            'fetch': False
        }

    def create(self, new_user=None):
        """Create the user

        Attempts to create the user. If the operation fails, an error is
        generated and printed.

        new_user[in]       MySQL user string (user@host:passwd)
                           (optional) If omitted, operation is performed
                           on the class instance user name.
        """

        query_str = "CREATE USER "
        user, passwd, host = None, None, None
        if new_user:
            user, passwd, host = parse_user_host(new_user)
            query_str += "'%s'@'%s' " % (user, host)
        else:
            query_str += "'%s'@'%s' " % (self.user, self.host)
            passwd = self.passwd

        if passwd:
            query_str += "IDENTIFIED BY '%s'" % (passwd)
        if self.verbosity > 0:
            print query_str

        self.server1.exec_query(query_str, self.query_options)

    def drop(self, new_user=None):
        """Drop user from the server

        Attempts to drop the user. If the operation fails, an error is
        generated and printed.

        new_user[in]       MySQL user string (user@host:passwd)
                           (optional) If omitted, operation is performed
                           on the class instance user name.
        """
        query_str = "DROP USER "
        if new_user:
            user, _, host = parse_user_host(new_user)
            query_str += "'%s'@'%s' " % (user, host)
        else:
            query_str += "'%s'@'%s' " % (self.user, self.host)

        if self.verbosity > 0:
            print query_str

        try:
            self.server1.exec_query(query_str, self.query_options)
        except UtilError:
            return False
        return True

    def exists(self, user_name=None):
        """Check to see if the user exists

        user_name[in]      MySQL user string (user@host:passwd)
                           (optional) If omitted, operation is performed
                           on the class instance user name.

        return True = user exists, False = user does not exist
        """

        user, host, _ = self.user, self.host, self.passwd
        if user_name:
            user, _, host = parse_user_host(user_name)

        res = self.server1.exec_query("SELECT * FROM mysql.user "
                                      "WHERE user = %s and host = %s",
                                      {'params': (user, host)})

        return (res is not None and len(res) >= 1)

    @staticmethod
    def _get_grants_as_dict(grant_list):
        """Transforms list of grant string statements into a dictionary.

        grant_list[in]    List of grant strings as returned from the server

        Returns a default_dict with the grant information
        """
        grant_dict = defaultdict(lambda: defaultdict(set))
        for grant in grant_list:
            grant_tpl = User._parse_grant_statement(grant[0])
            # Ignore PROXY privilege
            if 'PROXY' not in grant_tpl.privileges:
                grant_dict[grant_tpl.db][grant_tpl.object].update(
                    grant_tpl.privileges)
        return grant_dict

    def get_grants(self, globals_privs=False, as_dict=False, refresh=False):
        """Retrieve the grants for the current user

        globals_privs[in]     Include global privileges in clone (i.e. user@%)
        as_dict[in]           If True, instead of a list of plain grant
                              strings, return a dictionary with the grants.
        refresh[in]           If True, reads grant privileges directly from the
                              server and updates cached values, otherwise uses
                              the cached values.

        returns result set or None if no grants defined
        """

        # only read values from server if needed
        if refresh or not self.grant_list or not self.global_grant_list:
            # Get the users' connection user@host if not retrieved
            if self.current_user is None:
                res = self.server1.exec_query("SELECT CURRENT_USER()")
                parts = res[0][0].split('@')
                # If we're connected as some other user, use the user@host
                # defined at instantiation
                if parts[0] != self.user:
                    host = clean_IPv6(self.host)
                    self.current_user = "'%s'@'%s'" % (self.user, host)
                else:
                    self.current_user = "'%s'@'%s'" % (parts[0], parts[1])
            grants = []
            try:
                res = self.server1.exec_query("SHOW GRANTS FOR "
                                              "{0}".format(self.current_user))
                for grant in res:
                    grants.append(grant)
            except UtilDBError:
                pass  # Error here is ok - no grants found.

            # Cache user grants
            self.grant_list = grants[:]
            self.grant_dict = User._get_grants_as_dict(self.grant_list)
            # If current user is already using global host wildcard '%', there
            # is no need to run the show grants again.
            if globals_privs:
                if self.host != '%':
                    try:
                        res = self.server1.exec_query(
                            "SHOW GRANTS FOR '{0}'{1}".format(self.user,
                                                              "@'%'"))
                        for grant in res:
                            grants.append(grant)
                        self.global_grant_list = grants[:]
                        self.global_grant_dict = User._get_grants_as_dict(
                            self.global_grant_list)
                    except UtilDBError:
                        # User has no global privs, return the just the ones
                        # for current host
                        self.global_grant_list = self.grant_list
                        self.global_grant_dict = self.grant_dict
                else:
                    # if host is % then we already have the global privs
                    self.global_grant_list = self.grant_list
                    self.global_grant_dict = self.grant_dict

        if globals_privs:
            if as_dict:
                return self.global_grant_dict
            else:
                return self.global_grant_list
        else:
            if as_dict:
                return self.grant_dict
            else:
                return self.grant_list

    def has_privilege(self, db, obj, access, allow_skip_grant_tables=True):
        """Check to see user has a specific access to a db.object.

        db[in]             Name of database
        obj[in]            Name of object
        access[in]         MySQL privilege to check (e.g. SELECT, SUPER, DROP)
        allow_skip_grant_tables[in]  If True, allow silent failure for
                           cases where the server is started with
                           --skip-grant-tables. Default=True

        Returns True if user has access, False if not
        """
        grants_enabled = self.server1.grant_tables_enabled()
        # If grants are disabled and it is Ok to allow skipped grant tables,
        # return True - privileges disabled so user can do anything.
        if allow_skip_grant_tables and not grants_enabled:
            return True
        # Convert privilege to upper cases.
        access = access.upper()

        # Get grant dictionary
        grant_dict = self.get_grants(globals_privs=True, as_dict=True)

        # If self has all privileges for all databases, no need to check,
        # simply return True
        if ("ALL PRIVILEGES" in grant_dict['*']['*'] and
                "GRANT OPTION" in grant_dict['*']['*']):
            return True

        # Quote db and obj with backticks if necessary
        if not is_quoted_with_backticks(db) and db != '*':
            db = quote_with_backticks(db)

        if not is_quoted_with_backticks(obj) and obj != '*':
            obj = quote_with_backticks(obj)

        # USAGE privilege is the same as no privileges,
        # so everyone has it.
        if access == "USAGE":
            return True
        # Even if we have ALL PRIVILEGES grant, we might not have WITH GRANT
        # OPTION privilege.
        # Check server wide grants.
        elif (access in grant_dict['*']['*'] or
                "ALL PRIVILEGES" in grant_dict['*']['*'] and
                access != "GRANT OPTION"):
            return True
        # Check database level grants.
        elif (access in grant_dict[db]['*'] or
                "ALL PRIVILEGES" in grant_dict[db]['*'] and
                access != "GRANT OPTION"):
            return True
        # Check object level grants.
        elif (access in grant_dict[db][obj] or
                "ALL PRIVILEGES" in grant_dict[db][obj] and
                access != "GRANT OPTION"):
            return True
        else:
            return False

    def contains_user_privileges(self, user, plus_grant_option=False):
        """Checks if privileges of given user are a subset of self's privileges

        user[in]               instance of the user class
        plus_grant_option[in]  if True, checks if besides the all the other
                               privileges, self has also the GRANT OPTION
                               in all of the bd, tables in which the user
                               passed as argument has privileges. Required for
                               instance if we will be using self to clone the
                               user.
        return_missing[in]     if True, return a set with the missing grants
                               instead of simply a boolean value.

        Returns True if the grants of the user passed as argument
        are a subset of the grants of self, otherwise returns False.
        """
        user_grants = user.get_grants(as_dict=True)

        # If we are cloning User1, using User2, then User2 needs
        # the GRANT OPTION privilege in each of the db,table where
        # User1 has privileges.
        if plus_grant_option:
            for db in user_grants:
                for table in user_grants[db]:
                    priv_set = user_grants[db][table]
                    # Ignore empty grant sets that might exist as a
                    # consequence of consulting the defaultdict.
                    if priv_set:
                        # Ignore USAGE grant as it means no privileges.
                        if (len(priv_set) == 1 and
                                "USAGE" in priv_set):
                            continue
                        else:
                            priv_set.add('GRANT OPTION')

        for db in user_grants:
            for table in user_grants[db]:
                priv_set = user_grants[db][table]
                for priv in priv_set:
                    if self.has_privilege(db, table, priv):
                        continue
                    else:
                        return False
        return True

    def missing_user_privileges(self, user, plus_grant_option=False):
        """Checks if privileges of given user are a subset of self's privileges

        user[in]               instance of the user class
        plus_grant_option[in]  if True, checks if besides the all the other
                               privileges, self has also the GRANT OPTION
                               in all of the bd, tables in which the user
                               passed as argument has privileges. Required for
                               instance if we will be using self to clone the
                               user.
        return_missing[in]     if True, return a set with the missing grants
                               instead of simply a boolean value.

        Returns empty set if the grants of the user passed as argument
        are a subset of the grants of self, otherwise a set with the missing
        privileges from self.
        """
        user_grants = user.get_grants(as_dict=True)
        missing_grants = set()

        # If we are cloning User1, using User2, then User2 needs
        # the GRANT OPTION privilege in each of the db,table where
        # User1 has privileges.
        if plus_grant_option:
            for db in user_grants:
                for table in user_grants[db]:
                    priv_set = user_grants[db][table]
                    # Ignore empty grant sets that might exist as a
                    # consequence of consulting the defaultdict.
                    if priv_set:
                        # Ignore USAGE grant as it means no privileges.
                        if (len(priv_set) == 1 and
                                "USAGE" in priv_set):
                            continue
                        else:
                            priv_set.add('GRANT OPTION')

        for db in user_grants:
            for table in user_grants[db]:
                priv_set = user_grants[db][table]
                for priv in priv_set:
                    if self.has_privilege(db, table, priv):
                        continue
                    else:
                        missing_grants.add((priv, db, table))

        return missing_grants

    def print_grants(self):
        """Display grants for the current user"""

        res = self.get_grants(True)
        for grant_tuple in res:
            print grant_tuple[0]

    def clone(self, new_user, destination=None, globals_privs=False):
        """Clone the current user to the new user

        Operation will create the new user account copying all of the
        grants for the current user to the new user. If operation fails,
        an error message is generated and the process halts.

        new_name[in]       MySQL user string (user@host:passwd)
        destination[in]    A connection to a new server to clone the user
                           (default is None)
        globals_privs[in]  Include global privileges in clone (i.e. user@%)

        Note: Caller must ensure the new user account does not exist.
        """

        res = self.get_grants(globals_privs)
        server = self.server1
        if destination is not None:
            server = destination
        for row in res:
            # Create an instance of the user class.
            user = User(server, new_user, self.verbosity)
            if not user.exists():
                user.create()

            base_user_ticks = "'" + self.user + "'@'" + self.host + "'"
            user, _, host = parse_user_host(new_user)
            new_user_ticks = "'" + user + "'@'" + host + "'"
            grant = row[0].replace(base_user_ticks, new_user_ticks, 1)

            # Need to remove the IDENTIFIED BY clause for the base user.
            search_str = "IDENTIFIED BY PASSWORD"
            try:
                start = grant.index(search_str)
            except:
                start = 0

            if start > 0:
                end = grant.index("'", start + len(search_str) + 2) + 2
                grant = grant[0:start] + grant[end:]

            if self.verbosity > 0:
                print grant

            res = server.exec_query(grant, self.query_options)

    @staticmethod
    def _parse_grant_statement(statement):
        """ Returns a namedtuple with the parsed GRANT information.

        statement[in] Grant string in the sql format returned by the server.

        Returns named tuple with GRANT information or None.
        """

        grant_parse_re = re.compile(r"""
            GRANT\s(.+)?\sON\s # grant or list of grants
            (?:(?:PROCEDURE\s)|(?:FUNCTION\s))? # optional for routines only
            (?:(?:(\*|`?[^']+`?)\.(\*|`?[^']+`?)) # object where grant applies
            | ('[^']*'@'[^']*')) # For proxy grants user/host
            \sTO\s([^@]+@[\S]+) # grantee
            (?:\sIDENTIFIED\sBY\sPASSWORD\s\'[^\']+\')? # optional password
            (?:\sREQUIRE\sSSL)? # optional SSL
            (\sWITH\sGRANT\sOPTION)? # optional grant option
            $ # End of grant statement
            """, re.VERBOSE)

        grant_tpl_factory = namedtuple("grant_info", "privileges proxy_user "
                                                     "db object user")
        grants = None
        match = re.match(grant_parse_re, statement)

        if match:
            # quote database name and object name with backticks
            if match.group(1).upper() != 'PROXY':
                db = match.group(2)
                if not is_quoted_with_backticks(db) and db != '*':
                    db = quote_with_backticks(db)
                obj = match.group(3)
                if not is_quoted_with_backticks(obj) and obj != '*':
                    obj = quote_with_backticks(obj)
            else:  # if it is not a proxy grant
                db = obj = None
            grants = grant_tpl_factory(
                # privileges
                set([priv.strip() for priv in match.group(1).split(",")]),
                match.group(4),  # proxied user
                db,  # database
                obj,  # object
                match.group(5),  # user
            )
            # If user has grant option, add it to the list of privileges
            if match.group(6) is not None:
                grants.privileges.add("GRANT OPTION")
        else:
            raise UtilError("Unable to parse grant statement "
                            "{0}".format(statement))

        return grants
