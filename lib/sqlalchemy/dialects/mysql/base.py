# -*- fill-column: 78 -*-
# mysql.py
# Copyright (C) 2005, 2006, 2007, 2008, 2009 Michael Bayer mike_mp@zzzcomputing.com
#
# This module is part of SQLAlchemy and is released under
# the MIT License: http://www.opensource.org/licenses/mit-license.php

"""Support for the MySQL database.

Overview
--------

For normal SQLAlchemy usage, importing this module is unnecessary.  It will be
loaded on-demand when a MySQL connection is needed.  The generic column types
like :class:`~sqlalchemy.String` and :class:`~sqlalchemy.Integer` will
automatically be adapted to the optimal matching MySQL column type.

But if you would like to use one of the MySQL-specific or enhanced column
types when creating tables with your :class:`~sqlalchemy.Table` definitions,
then you will need to import them from this module::

  from sqlalchemy.dialect.mysql import base as mysql

  Table('mytable', metadata,
        Column('id', Integer, primary_key=True),
        Column('ittybittyblob', mysql.MSTinyBlob),
        Column('biggy', mysql.MSBigInteger(unsigned=True)))

All standard MySQL column types are supported.  The OpenGIS types are
available for use via table reflection but have no special support or mapping
to Python classes.  If you're using these types and have opinions about how
OpenGIS can be smartly integrated into SQLAlchemy please join the mailing
list!

Supported Versions and Features
-------------------------------

SQLAlchemy supports 6 major MySQL versions: 3.23, 4.0, 4.1, 5.0, 5.1 and 6.0,
with capabilities increasing with more modern servers.

Versions 4.1 and higher support the basic SQL functionality that SQLAlchemy
uses in the ORM and SQL expressions.  These versions pass the applicable tests
in the suite 100%.  No heroic measures are taken to work around major missing
SQL features- if your server version does not support sub-selects, for
example, they won't work in SQLAlchemy either.

Currently, the only DB-API driver supported is `MySQL-Python` (also referred to
as `MySQLdb`).  Either 1.2.1 or 1.2.2 are recommended.  The alpha, beta and
gamma releases of 1.2.1 and 1.2.2 should be avoided.  Support for Jython and
IronPython is planned.

=====================================  ===============
Feature                                Minimum Version
=====================================  ===============
sqlalchemy.orm                         4.1.1
Table Reflection                       3.23.x
DDL Generation                         4.1.1
utf8/Full Unicode Connections          4.1.1
Transactions                           3.23.15
Two-Phase Transactions                 5.0.3
Nested Transactions                    5.0.3
=====================================  ===============

See the official MySQL documentation for detailed information about features
supported in any given server release.

Storage Engines
---------------

Most MySQL server installations have a default table type of ``MyISAM``, a
non-transactional table type.  During a transaction, non-transactional storage
engines do not participate and continue to store table changes in autocommit
mode.  For fully atomic transactions, all participating tables must use a
transactional engine such as ``InnoDB``, ``Falcon``, ``SolidDB``, `PBXT`, etc.

Storage engines can be elected when creating tables in SQLAlchemy by supplying
a ``mysql_engine='whatever'`` to the ``Table`` constructor.  Any MySQL table
creation option can be specified in this syntax::

  Table('mytable', metadata,
        Column('data', String(32)),
        mysql_engine='InnoDB',
        mysql_charset='utf8'
       )

Keys
----

Not all MySQL storage engines support foreign keys.  For ``MyISAM`` and
similar engines, the information loaded by table reflection will not include
foreign keys.  For these tables, you may supply a
:class:`~sqlalchemy.ForeignKeyConstraint` at reflection time::

  Table('mytable', metadata,
        ForeignKeyConstraint(['other_id'], ['othertable.other_id']),
        autoload=True
       )

When creating tables, SQLAlchemy will automatically set ``AUTO_INCREMENT``` on
an integer primary key column::

  >>> t = Table('mytable', metadata,
  ...   Column('mytable_id', Integer, primary_key=True)
  ... )
  >>> t.create()
  CREATE TABLE mytable (
          id INTEGER NOT NULL AUTO_INCREMENT,
          PRIMARY KEY (id)
  )

You can disable this behavior by supplying ``autoincrement=False`` to the
:class:`~sqlalchemy.Column`.  This flag can also be used to enable
auto-increment on a secondary column in a multi-column key for some storage
engines::

  Table('mytable', metadata,
        Column('gid', Integer, primary_key=True, autoincrement=False),
        Column('id', Integer, primary_key=True)
       )

SQL Mode
--------

MySQL SQL modes are supported.  Modes that enable ``ANSI_QUOTES`` (such as
``ANSI``) require an engine option to modify SQLAlchemy's quoting style.
When using an ANSI-quoting mode, supply ``use_ansiquotes=True`` when
creating your ``Engine``::

  create_engine('mysql://localhost/test', use_ansiquotes=True)

This is an engine-wide option and is not toggleable on a per-connection basis.
SQLAlchemy does not presume to ``SET sql_mode`` for you with this option.  For
the best performance, set the quoting style server-wide in ``my.cnf`` or by
supplying ``--sql-mode`` to ``mysqld``.  You can also use a
:class:`sqlalchemy.pool.Pool` listener hook to issue a ``SET SESSION
sql_mode='...'`` on connect to configure each connection.

If you do not specify ``use_ansiquotes``, the regular MySQL quoting style is
used by default.

If you do issue a ``SET sql_mode`` through SQLAlchemy, the dialect must be
updated if the quoting style is changed.  Again, this change will affect all
connections::

  connection.execute('SET sql_mode="ansi"')
  connection.dialect.use_ansiquotes = True

MySQL SQL Extensions
--------------------

Many of the MySQL SQL extensions are handled through SQLAlchemy's generic
function and operator support::

  table.select(table.c.password==func.md5('plaintext'))
  table.select(table.c.username.op('regexp')('^[a-d]'))

And of course any valid MySQL statement can be executed as a string as well.

Some limited direct support for MySQL extensions to SQL is currently
available.

  * SELECT pragma::

      select(..., prefixes=['HIGH_PRIORITY', 'SQL_SMALL_RESULT'])

  * UPDATE with LIMIT::

      update(..., mysql_limit=10)

Troubleshooting
---------------

If you have problems that seem server related, first check that you are
using the most recent stable MySQL-Python package available.  The Database
Notes page on the wiki at http://www.sqlalchemy.org is a good resource for
timely information affecting MySQL in SQLAlchemy.

"""

import datetime, decimal, inspect, re, sys

from sqlalchemy import exc, log, schema, sql, util
from sqlalchemy.sql import operators as sql_operators
from sqlalchemy.sql import functions as sql_functions
from sqlalchemy.sql import compiler

from sqlalchemy.engine import base as engine_base, default
from sqlalchemy import types as sqltypes


__all__ = (
    'MSBigInteger', 'MSMediumInteger', 'MSBinary', 'MSBit', 'MSBlob', 'MSBoolean',
    'MSChar', 'MSDate', 'MSDateTime', 'MSDecimal', 'MSDouble',
    'MSEnum', 'MSFloat', 'MSInteger', 'MSLongBlob', 'MSLongText',
    'MSMediumBlob', 'MSMediumText', 'MSNChar', 'MSNVarChar',
    'MSNumeric', 'MSSet', 'MSSmallInteger', 'MSString', 'MSText',
    'MSTime', 'MSTimeStamp', 'MSTinyBlob', 'MSTinyInteger',
    'MSTinyText', 'MSVarBinary', 'MSYear' )


RESERVED_WORDS = set(
    ['accessible', 'add', 'all', 'alter', 'analyze','and', 'as', 'asc',
     'asensitive', 'before', 'between', 'bigint', 'binary', 'blob', 'both',
     'by', 'call', 'cascade', 'case', 'change', 'char', 'character', 'check',
     'collate', 'column', 'condition', 'constraint', 'continue', 'convert',
     'create', 'cross', 'current_date', 'current_time', 'current_timestamp',
     'current_user', 'cursor', 'database', 'databases', 'day_hour',
     'day_microsecond', 'day_minute', 'day_second', 'dec', 'decimal',
     'declare', 'default', 'delayed', 'delete', 'desc', 'describe',
     'deterministic', 'distinct', 'distinctrow', 'div', 'double', 'drop',
     'dual', 'each', 'else', 'elseif', 'enclosed', 'escaped', 'exists',
     'exit', 'explain', 'false', 'fetch', 'float', 'float4', 'float8',
     'for', 'force', 'foreign', 'from', 'fulltext', 'grant', 'group', 'having',
     'high_priority', 'hour_microsecond', 'hour_minute', 'hour_second', 'if',
     'ignore', 'in', 'index', 'infile', 'inner', 'inout', 'insensitive',
     'insert', 'int', 'int1', 'int2', 'int3', 'int4', 'int8', 'integer',
     'interval', 'into', 'is', 'iterate', 'join', 'key', 'keys', 'kill',
     'leading', 'leave', 'left', 'like', 'limit', 'linear', 'lines', 'load',
     'localtime', 'localtimestamp', 'lock', 'long', 'longblob', 'longtext',
     'loop', 'low_priority', 'master_ssl_verify_server_cert', 'match',
     'mediumblob', 'mediumint', 'mediumtext', 'middleint',
     'minute_microsecond', 'minute_second', 'mod', 'modifies', 'natural',
     'not', 'no_write_to_binlog', 'null', 'numeric', 'on', 'optimize',
     'option', 'optionally', 'or', 'order', 'out', 'outer', 'outfile',
     'precision', 'primary', 'procedure', 'purge', 'range', 'read', 'reads',
     'read_only', 'read_write', 'real', 'references', 'regexp', 'release',
     'rename', 'repeat', 'replace', 'require', 'restrict', 'return',
     'revoke', 'right', 'rlike', 'schema', 'schemas', 'second_microsecond',
     'select', 'sensitive', 'separator', 'set', 'show', 'smallint', 'spatial',
     'specific', 'sql', 'sqlexception', 'sqlstate', 'sqlwarning',
     'sql_big_result', 'sql_calc_found_rows', 'sql_small_result', 'ssl',
     'starting', 'straight_join', 'table', 'terminated', 'then', 'tinyblob',
     'tinyint', 'tinytext', 'to', 'trailing', 'trigger', 'true', 'undo',
     'union', 'unique', 'unlock', 'unsigned', 'update', 'usage', 'use',
     'using', 'utc_date', 'utc_time', 'utc_timestamp', 'values', 'varbinary',
     'varchar', 'varcharacter', 'varying', 'when', 'where', 'while', 'with',
     'write', 'x509', 'xor', 'year_month', 'zerofill', # 5.0
     'columns', 'fields', 'privileges', 'soname', 'tables', # 4.1
     'accessible', 'linear', 'master_ssl_verify_server_cert', 'range',
     'read_only', 'read_write', # 5.1
     ])

AUTOCOMMIT_RE = re.compile(
    r'\s*(?:UPDATE|INSERT|CREATE|DELETE|DROP|ALTER|LOAD +DATA|REPLACE)',
    re.I | re.UNICODE)
SET_RE = re.compile(
    r'\s*SET\s+(?:(?:GLOBAL|SESSION)\s+)?\w',
    re.I | re.UNICODE)


class _NumericType(object):
    """Base for MySQL numeric types."""

    def __init__(self, kw):
        self.unsigned = kw.pop('unsigned', False)
        self.zerofill = kw.pop('zerofill', False)


class _StringType(object):
    """Base for MySQL string types."""

    def __init__(self, charset=None, collation=None,
                 ascii=False, unicode=False, binary=False,
                 national=False, **kwargs):
        self.charset = charset
        # allow collate= or collation=
        self.collation = kwargs.get('collate', collation)
        self.ascii = ascii
        self.unicode = unicode
        self.binary = binary
        self.national = national

    def __repr__(self):
        attributes = inspect.getargspec(self.__init__)[0][1:]
        attributes.extend(inspect.getargspec(_StringType.__init__)[0][1:])

        params = {}
        for attr in attributes:
            val = getattr(self, attr)
            if val is not None and val is not False:
                params[attr] = val

        return "%s(%s)" % (self.__class__.__name__,
                           ', '.join(['%s=%r' % (k, params[k]) for k in params]))


class MSNumeric(sqltypes.Numeric, _NumericType):
    """MySQL NUMERIC type."""
    
    __visit_name__ = 'NUMERIC'
    
    def __init__(self, precision=10, scale=2, asdecimal=True, **kw):
        """Construct a NUMERIC.

        :param precision: Total digits in this number.  If scale and precision
          are both None, values are stored to limits allowed by the server.

        :param scale: The number of digits after the decimal point.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        _NumericType.__init__(self, kw)
        sqltypes.Numeric.__init__(self, precision, scale, asdecimal=asdecimal, **kw)

    def bind_processor(self, dialect):
        return None

    def result_processor(self, dialect):
        if not self.asdecimal:
            def process(value):
                if isinstance(value, decimal.Decimal):
                    return float(value)
                else:
                    return value
            return process
        else:
            return None


class MSDecimal(MSNumeric):
    """MySQL DECIMAL type."""
    
    __visit_name__ = 'DECIMAL'
    
    def __init__(self, precision=10, scale=2, asdecimal=True, **kw):
        """Construct a DECIMAL.

        :param precision: Total digits in this number.  If scale and precision
          are both None, values are stored to limits allowed by the server.

        :param scale: The number of digits after the decimal point.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        super(MSDecimal, self).__init__(precision, scale, asdecimal=asdecimal, **kw)


class MSDouble(sqltypes.Float, _NumericType):
    """MySQL DOUBLE type."""

    __visit_name__ = 'DOUBLE'

    def __init__(self, precision=None, scale=None, asdecimal=True, **kw):
        """Construct a DOUBLE.

        :param precision: Total digits in this number.  If scale and precision
          are both None, values are stored to limits allowed by the server.

        :param scale: The number of digits after the decimal point.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        if ((precision is None and scale is not None) or
            (precision is not None and scale is None)):
            raise exc.ArgumentError(
                "You must specify both precision and scale or omit "
                "both altogether.")

        _NumericType.__init__(self, kw)
        sqltypes.Float.__init__(self, asdecimal=asdecimal, **kw)
        self.scale = scale
        self.precision = precision


class MSReal(MSDouble):
    """MySQL REAL type."""

    __visit_name__ = 'REAL'

    def __init__(self, precision=None, scale=None, asdecimal=True, **kw):
        """Construct a REAL.

        :param precision: Total digits in this number.  If scale and precision
          are both None, values are stored to limits allowed by the server.

        :param scale: The number of digits after the decimal point.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        MSDouble.__init__(self, precision, scale, asdecimal, **kw)


class MSFloat(sqltypes.Float, _NumericType):
    """MySQL FLOAT type."""

    __visit_name__ = 'FLOAT'

    def __init__(self, precision=None, scale=None, asdecimal=False, **kw):
        """Construct a FLOAT.

        :param precision: Total digits in this number.  If scale and precision
          are both None, values are stored to limits allowed by the server.

        :param scale: The number of digits after the decimal point.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        _NumericType.__init__(self, kw)
        sqltypes.Float.__init__(self, asdecimal=asdecimal, **kw)
        self.scale = scale
        self.precision = precision

    def bind_processor(self, dialect):
        return None


class MSInteger(sqltypes.Integer, _NumericType):
    """MySQL INTEGER type."""

    __visit_name__ = 'INTEGER'

    def __init__(self, display_width=None, **kw):
        """Construct an INTEGER.

        :param display_width: Optional, maximum display width for this number.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        if 'length' in kw:
            util.warn_deprecated("'length' is deprecated for MSInteger and subclasses.  Use 'display_width'.")
            self.display_width = kw.pop('length')
        else:
            self.display_width = display_width
        _NumericType.__init__(self, kw)
        sqltypes.Integer.__init__(self, **kw)


class MSBigInteger(MSInteger):
    """MySQL BIGINTEGER type."""

    __visit_name__ = 'BIGINT'

    def __init__(self, display_width=None, **kw):
        """Construct a BIGINTEGER.

        :param display_width: Optional, maximum display width for this number.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        super(MSBigInteger, self).__init__(display_width, **kw)


class MSMediumInteger(MSInteger):
    """MySQL MEDIUMINTEGER type."""

    __visit_name__ = 'MEDIUMINT'

    def __init__(self, display_width=None, **kw):
        """Construct a MEDIUMINTEGER

        :param display_width: Optional, maximum display width for this number.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        super(MSMediumInteger, self).__init__(display_width, **kw)


class MSTinyInteger(MSInteger):
    """MySQL TINYINT type."""

    __visit_name__ = 'TINYINT'

    def __init__(self, display_width=None, **kw):
        """Construct a TINYINT.

        Note: following the usual MySQL conventions, TINYINT(1) columns
        reflected during Table(..., autoload=True) are treated as
        Boolean columns.

        :param display_width: Optional, maximum display width for this number.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        super(MSTinyInteger, self).__init__(display_width, **kw)


class MSSmallInteger(sqltypes.SmallInteger, MSInteger):
    """MySQL SMALLINTEGER type."""

    __visit_name__ = 'SMALLINT'

    def __init__(self, display_width=None, **kw):
        """Construct a SMALLINTEGER.

        :param display_width: Optional, maximum display width for this number.

        :param unsigned: a boolean, optional.

        :param zerofill: Optional. If true, values will be stored as strings
          left-padded with zeros. Note that this does not effect the values
          returned by the underlying database API, which continue to be
          numeric.

        """
        self.display_width = display_width
        _NumericType.__init__(self, kw)
        sqltypes.SmallInteger.__init__(self, **kw)


class MSBit(sqltypes.TypeEngine):
    """MySQL BIT type.

    This type is for MySQL 5.0.3 or greater for MyISAM, and 5.0.5 or greater for
    MyISAM, MEMORY, InnoDB and BDB.  For older versions, use a MSTinyInteger()
    type.

    """

    __visit_name__ = 'BIT'

    def __init__(self, length=None):
        """Construct a BIT.

        :param length: Optional, number of bits.

        """
        self.length = length

    def result_processor(self, dialect):
        """Convert a MySQL's 64 bit, variable length binary string to a long."""
        def process(value):
            if value is not None:
                v = 0L
                for i in map(ord, value):
                    v = v << 8 | i
                value = v
            return value
        return process

# TODO: probably don't need datetime/date types since no behavior changes

class MSDateTime(sqltypes.DateTime):
    """MySQL DATETIME type."""
    
    __visit_name__ = 'DATETIME'


class MSDate(sqltypes.Date):
    """MySQL DATE type."""
    __visit_name__ = 'DATE'



class MSTime(sqltypes.Time):
    """MySQL TIME type."""

    __visit_name__ = 'TIME'

    def result_processor(self, dialect):
        def process(value):
            # convert from a timedelta value
            if value is not None:
                return datetime.time(value.seconds/60/60, value.seconds/60%60, value.seconds - (value.seconds/60*60))
            else:
                return None
        return process

class MSTimeStamp(sqltypes.TIMESTAMP):
    """MySQL TIMESTAMP type.

    To signal the orm to automatically re-select modified rows to retrieve the
    updated timestamp, add a ``server_default`` to your
    :class:`~sqlalchemy.Column` specification::

        from sqlalchemy.databases import mysql
        Column('updated', mysql.MSTimeStamp,
               server_default=sql.text('CURRENT_TIMESTAMP')
              )

    The full range of MySQL 4.1+ TIMESTAMP defaults can be specified in
    the the default::

        server_default=sql.text('CURRENT TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')

    """
    __visit_name__ = 'TIMESTAMP'


class MSYear(sqltypes.TypeEngine):
    """MySQL YEAR type, for single byte storage of years 1901-2155."""

    __visit_name__ = 'YEAR'

    def __init__(self, display_width=None):
        self.display_width = display_width


class MSText(_StringType, sqltypes.Text):
    """MySQL TEXT type, for text up to 2^16 characters."""

    __visit_name__ = 'TEXT'

    def __init__(self, length=None, **kwargs):
        """Construct a TEXT.

        :param length: Optional, if provided the server may optimize storage
          by substituting the smallest TEXT type sufficient to store
          ``length`` characters.

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param national: Optional. If true, use the server's configured
          national character set.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        """
        _StringType.__init__(self, **kwargs)
        sqltypes.Text.__init__(self, length,
                               kwargs.get('convert_unicode', False), kwargs.get('assert_unicode', None))


class MSTinyText(MSText):
    """MySQL TINYTEXT type, for text up to 2^8 characters."""

    __visit_name__ = 'TINYTEXT'

    def __init__(self, **kwargs):
        """Construct a TINYTEXT.

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param national: Optional. If true, use the server's configured
          national character set.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        """

        super(MSTinyText, self).__init__(**kwargs)


class MSMediumText(MSText):
    """MySQL MEDIUMTEXT type, for text up to 2^24 characters."""

    __visit_name__ = 'MEDIUMTEXT'

    def __init__(self, **kwargs):
        """Construct a MEDIUMTEXT.

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param national: Optional. If true, use the server's configured
          national character set.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        """
        super(MSMediumText, self).__init__(**kwargs)

class MSLongText(MSText):
    """MySQL LONGTEXT type, for text up to 2^32 characters."""

    __visit_name__ = 'LONGTEXT'

    def __init__(self, **kwargs):
        """Construct a LONGTEXT.

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param national: Optional. If true, use the server's configured
          national character set.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        """
        super(MSLongText, self).__init__(**kwargs)



class MSString(_StringType, sqltypes.String):
    """MySQL VARCHAR type, for variable-length character data."""

    __visit_name__ = 'VARCHAR'

    def __init__(self, length=None, **kwargs):
        """Construct a VARCHAR.

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param national: Optional. If true, use the server's configured
          national character set.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        """
        _StringType.__init__(self, **kwargs)
        sqltypes.String.__init__(self, length,
                                 kwargs.get('convert_unicode', False), kwargs.get('assert_unicode', None))


class MSChar(_StringType, sqltypes.CHAR):
    """MySQL CHAR type, for fixed-length character data."""

    __visit_name__ = 'CHAR'

    def __init__(self, length, **kwargs):
        """Construct a CHAR.

        :param length: Maximum data length, in characters.

        :param binary: Optional, use the default binary collation for the
          national character set.  This does not affect the type of data
          stored, use a BINARY type for binary data.

        :param collation: Optional, request a particular collation.  Must be
          compatible with the national character set.

        """
        _StringType.__init__(self, **kwargs)
        sqltypes.CHAR.__init__(self, length,
                               kwargs.get('convert_unicode', False))



class MSNVarChar(_StringType, sqltypes.String):
    """MySQL NVARCHAR type.

    For variable-length character data in the server's configured national
    character set.
    """

    __visit_name__ = 'NVARCHAR'

    def __init__(self, length=None, **kwargs):
        """Construct an NVARCHAR.

        :param length: Maximum data length, in characters.

        :param binary: Optional, use the default binary collation for the
          national character set.  This does not affect the type of data
          stored, use a BINARY type for binary data.

        :param collation: Optional, request a particular collation.  Must be
          compatible with the national character set.

        """
        kwargs['national'] = True
        _StringType.__init__(self, **kwargs)
        sqltypes.String.__init__(self, length,
                                 kwargs.get('convert_unicode', False))



class MSNChar(_StringType, sqltypes.CHAR):
    """MySQL NCHAR type.

    For fixed-length character data in the server's configured national
    character set.
    """

    __visit_name__ = 'NCHAR'

    def __init__(self, length=None, **kwargs):
        """Construct an NCHAR.  Arguments are:

        :param length: Maximum data length, in characters.

        :param binary: Optional, use the default binary collation for the
          national character set.  This does not affect the type of data
          stored, use a BINARY type for binary data.

        :param collation: Optional, request a particular collation.  Must be
          compatible with the national character set.

        """
        kwargs['national'] = True
        _StringType.__init__(self, **kwargs)
        sqltypes.CHAR.__init__(self, length,
                               kwargs.get('convert_unicode', False))


class _BinaryType(sqltypes.Binary):
    """Base for MySQL binary types."""

    def result_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            else:
                return util.buffer(value)
        return process

class MSVarBinary(_BinaryType):
    """MySQL VARBINARY type, for variable length binary data."""

    __visit_name__ = 'VARBINARY'

    def __init__(self, length=None, **kw):
        """Construct a VARBINARY.  Arguments are:

        :param length: Maximum data length, in characters.

        """
        super(MSVarBinary, self).__init__(length, **kw)


class MSBinary(_BinaryType):
    """MySQL BINARY type, for fixed length binary data"""

    __visit_name__ = 'BINARY'

    def __init__(self, length=None, **kw):
        """Construct a BINARY.

        This is a fixed length type, and short values will be right-padded
        with a server-version-specific pad value.

        :param length: Maximum data length, in bytes.  If length is not
          specified, this will generate a BLOB.  This usage is deprecated.

        """
        super(MSBinary, self).__init__(length, **kw)

    def result_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            else:
                return util.buffer(value)
        return process

class MSBlob(_BinaryType):
    """MySQL BLOB type, for binary data up to 2^16 bytes"""

    __visit_name__ = 'BLOB'

    def __init__(self, length=None, **kw):
        """Construct a BLOB.  Arguments are:

        :param length: Optional, if provided the server may optimize storage
          by substituting the smallest TEXT type sufficient to store
          ``length`` characters.

        """
        super(MSBlob, self).__init__(length, **kw)

    def result_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            else:
                return util.buffer(value)
        return process

    def __repr__(self):
        return "%s()" % self.__class__.__name__


class MSTinyBlob(MSBlob):
    """MySQL TINYBLOB type, for binary data up to 2^8 bytes."""
    
    __visit_name__ = 'TINYBLOB'


class MSMediumBlob(MSBlob):
    """MySQL MEDIUMBLOB type, for binary data up to 2^24 bytes."""

    __visit_name__ = 'MEDIUMBLOB'


class MSLongBlob(MSBlob):
    """MySQL LONGBLOB type, for binary data up to 2^32 bytes."""

    __visit_name__ = 'LONGBLOB'


class MSEnum(MSString):
    """MySQL ENUM type."""

    __visit_name__ = 'ENUM'

    def __init__(self, *enums, **kw):
        """Construct an ENUM.

        Example:

          Column('myenum', MSEnum("foo", "bar", "baz"))

        Arguments are:

        :param enums: The range of valid values for this ENUM.  Values will be
          quoted when generating the schema according to the quoting flag (see
          below).

        :param strict: Defaults to False: ensure that a given value is in this
          ENUM's range of permissible values when inserting or updating rows.
          Note that MySQL will not raise a fatal error if you attempt to store
          an out of range value- an alternate value will be stored instead.
          (See MySQL ENUM documentation.)

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        :param quoting: Defaults to 'auto': automatically determine enum value
          quoting.  If all enum values are surrounded by the same quoting
          character, then use 'quoted' mode.  Otherwise, use 'unquoted' mode.

          'quoted': values in enums are already quoted, they will be used
          directly when generating the schema.

          'unquoted': values in enums are not quoted, they will be escaped and
          surrounded by single quotes when generating the schema.

          Previous versions of this type always required manually quoted
          values to be supplied; future versions will always quote the string
          literals for you.  This is a transitional option.

        """
        self.quoting = kw.pop('quoting', 'auto')

        if self.quoting == 'auto':
            # What quoting character are we using?
            q = None
            for e in enums:
                if len(e) == 0:
                    self.quoting = 'unquoted'
                    break
                elif q is None:
                    q = e[0]

                if e[0] != q or e[-1] != q:
                    self.quoting = 'unquoted'
                    break
            else:
                self.quoting = 'quoted'

        if self.quoting == 'quoted':
            util.warn_pending_deprecation(
                'Manually quoting ENUM value literals is deprecated.  Supply '
                'unquoted values and use the quoting= option in cases of '
                'ambiguity.')
            strip_enums = []
            for a in enums:
                if a[0:1] == '"' or a[0:1] == "'":
                    # strip enclosing quotes and unquote interior
                    a = a[1:-1].replace(a[0] * 2, a[0])
                strip_enums.append(a)
            self.enums = strip_enums
        else:
            self.enums = list(enums)

        self.strict = kw.pop('strict', False)
        length = max([len(v) for v in self.enums] + [0])
        super(MSEnum, self).__init__(length, **kw)

    def bind_processor(self, dialect):
        super_convert = super(MSEnum, self).bind_processor(dialect)
        def process(value):
            if self.strict and value is not None and value not in self.enums:
                raise exc.InvalidRequestError('"%s" not a valid value for '
                                                     'this enum' % value)
            if super_convert:
                return super_convert(value)
            else:
                return value
        return process

class MSSet(MSString):
    """MySQL SET type."""

    __visit_name__ = 'SET'

    def __init__(self, *values, **kw):
        """Construct a SET.

        Example::

          Column('myset', MSSet("'foo'", "'bar'", "'baz'"))

        Arguments are:

        :param values: The range of valid values for this SET.  Values will be
          used exactly as they appear when generating schemas.  Strings must
          be quoted, as in the example above.  Single-quotes are suggested for
          ANSI compatibility and are required for portability to servers with
          ANSI_QUOTES enabled.

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        """
        self._ddl_values = values

        strip_values = []
        for a in values:
            if a[0:1] == '"' or a[0:1] == "'":
                # strip enclosing quotes and unquote interior
                a = a[1:-1].replace(a[0] * 2, a[0])
            strip_values.append(a)

        self.values = strip_values
        length = max([len(v) for v in strip_values] + [0])
        super(MSSet, self).__init__(length, **kw)

    def result_processor(self, dialect):
        def process(value):
            # The good news:
            #   No ',' quoting issues- commas aren't allowed in SET values
            # The bad news:
            #   Plenty of driver inconsistencies here.
            if isinstance(value, util.set_types):
                # ..some versions convert '' to an empty set
                if not value:
                    value.add('')
                # ..some return sets.Set, even for pythons that have __builtin__.set
                if not isinstance(value, set):
                    value = set(value)
                return value
            # ...and some versions return strings
            if value is not None:
                return set(value.split(','))
            else:
                return value
        return process

    def bind_processor(self, dialect):
        super_convert = super(MSSet, self).bind_processor(dialect)
        def process(value):
            if value is None or isinstance(value, (int, long, basestring)):
                pass
            else:
                if None in value:
                    value = set(value)
                    value.remove(None)
                    value.add('')
                value = ','.join(value)
            if super_convert:
                return super_convert(value)
            else:
                return value
        return process


class MSBoolean(sqltypes.Boolean):
    """MySQL BOOLEAN type."""

    __visit_name__ = 'BOOLEAN'

    def result_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return value and True or False
        return process

    def bind_processor(self, dialect):
        def process(value):
            if value is True:
                return 1
            elif value is False:
                return 0
            elif value is None:
                return None
            else:
                return value and True or False
        return process

colspecs = {
    sqltypes.Integer: MSInteger,
    sqltypes.SmallInteger: MSSmallInteger,
    sqltypes.Numeric: MSNumeric,
    sqltypes.Float: MSFloat,
    sqltypes.DateTime: MSDateTime,
    sqltypes.Date: MSDate,
    sqltypes.Time: MSTime,
    sqltypes.String: MSString,
    sqltypes.Binary: MSBlob,
    sqltypes.Boolean: MSBoolean,
    sqltypes.Text: MSText,
    sqltypes.CHAR: MSChar,
    sqltypes.NCHAR: MSNChar,
    sqltypes.TIMESTAMP: MSTimeStamp,
    sqltypes.BLOB: MSBlob,
    MSDouble: MSDouble,
    MSReal: MSReal,
    _BinaryType: _BinaryType,
}

# Everything 3.23 through 5.1 excepting OpenGIS types.
ischema_names = {
    'bigint': MSBigInteger,
    'binary': MSBinary,
    'bit': MSBit,
    'blob': MSBlob,
    'boolean':MSBoolean,
    'char': MSChar,
    'date': MSDate,
    'datetime': MSDateTime,
    'decimal': MSDecimal,
    'double': MSDouble,
    'enum': MSEnum,
    'fixed': MSDecimal,
    'float': MSFloat,
    'int': MSInteger,
    'integer': MSInteger,
    'longblob': MSLongBlob,
    'longtext': MSLongText,
    'mediumblob': MSMediumBlob,
    'mediumint': MSMediumInteger,
    'mediumtext': MSMediumText,
    'nchar': MSNChar,
    'nvarchar': MSNVarChar,
    'numeric': MSNumeric,
    'set': MSSet,
    'smallint': MSSmallInteger,
    'text': MSText,
    'time': MSTime,
    'timestamp': MSTimeStamp,
    'tinyblob': MSTinyBlob,
    'tinyint': MSTinyInteger,
    'tinytext': MSTinyText,
    'varbinary': MSVarBinary,
    'varchar': MSString,
    'year': MSYear,
}

class MySQLExecutionContext(default.DefaultExecutionContext):
    def post_exec(self):
        if self.isinsert and not self.executemany:
            if (not len(self._last_inserted_ids) or
                self._last_inserted_ids[0] is None):
                self._last_inserted_ids = ([self._lastrowid(self.cursor)] +
                                           self._last_inserted_ids[1:])
        elif (not self.isupdate and not self.should_autocommit and
              self.statement and SET_RE.match(self.statement)):
            # This misses if a user forces autocommit on text('SET NAMES'),
            # which is probably a programming error anyhow.
            self.connection.info.pop(('mysql', 'charset'), None)

    def _lastrowid(self, cursor):
        raise NotImplementedError()
    
    def should_autocommit_text(self, statement):
        return AUTOCOMMIT_RE.match(statement)

class MySQLCompiler(compiler.SQLCompiler):
    operators = compiler.SQLCompiler.operators.copy()
    operators.update({
        sql_operators.concat_op: lambda x, y: "concat(%s, %s)" % (x, y),
        sql_operators.mod: '%%',
        sql_operators.match_op: lambda x, y: "MATCH (%s) AGAINST (%s IN BOOLEAN MODE)" % (x, y)
    })
    functions = compiler.SQLCompiler.functions.copy()
    functions.update ({
        sql_functions.random: 'rand%(expr)s',
        "utc_timestamp":"UTC_TIMESTAMP"
        })

    def visit_typeclause(self, typeclause):
        type_ = typeclause.type.dialect_impl(self.dialect)
        if isinstance(type_, MSInteger):
            if getattr(type_, 'unsigned', False):
                return 'UNSIGNED INTEGER'
            else:
                return 'SIGNED INTEGER'
        elif isinstance(type_, (MSDecimal, MSDateTime, MSDate, MSTime)):
            return self.dialect.type_compiler.process(type_)
        elif isinstance(type_, MSText):
            return 'CHAR'
        elif (isinstance(type_, _StringType) and not
              isinstance(type_, (MSEnum, MSSet))):
            if getattr(type_, 'length'):
                return 'CHAR(%s)' % type_.length
            else:
                return 'CHAR'
        elif isinstance(type_, _BinaryType):
            return 'BINARY'
        elif isinstance(type_, MSNumeric):
            return self.dialect.type_compiler.process(type_).replace('NUMERIC', 'DECIMAL')
        elif isinstance(type_, MSTimeStamp):
            return 'DATETIME'
        elif isinstance(type_, (MSDateTime, MSDate, MSTime)):
            return self.dialect.type_compiler.process(type_)
        else:
            return None

    def visit_cast(self, cast, **kwargs):
        # No cast until 4, no decimals until 5.
        type_ = self.process(cast.typeclause)
        if type_ is None:
            return self.process(cast.clause)

        return 'CAST(%s AS %s)' % (self.process(cast.clause), type_)


    def post_process_text(self, text):
        if '%%' in text:
            util.warn("The SQLAlchemy MySQLDB dialect now automatically escapes '%' in text() expressions to '%%'.")
        return text.replace('%', '%%')

    def get_select_precolumns(self, select):
        if isinstance(select._distinct, basestring):
            return select._distinct.upper() + " "
        elif select._distinct:
            return "DISTINCT "
        else:
            return ""

    def visit_join(self, join, asfrom=False, **kwargs):
        # 'JOIN ... ON ...' for inner joins isn't available until 4.0.
        # Apparently < 3.23.17 requires theta joins for inner joins
        # (but not outer).  Not generating these currently, but
        # support can be added, preferably after dialects are
        # refactored to be version-sensitive.
        return ''.join(
            (self.process(join.left, asfrom=True),
             (join.isouter and " LEFT OUTER JOIN " or " INNER JOIN "),
             self.process(join.right, asfrom=True),
             " ON ",
             self.process(join.onclause)))

    def for_update_clause(self, select):
        if select.for_update == 'read':
            return ' LOCK IN SHARE MODE'
        else:
            return super(MySQLCompiler, self).for_update_clause(select)

    def limit_clause(self, select):
        # MySQL supports:
        #   LIMIT <limit>
        #   LIMIT <offset>, <limit>
        # and in server versions > 3.3:
        #   LIMIT <limit> OFFSET <offset>
        # The latter is more readable for offsets but we're stuck with the
        # former until we can refine dialects by server revision.

        limit, offset = select._limit, select._offset

        if (limit, offset) == (None, None):
            return ''
        elif offset is not None:
            # As suggested by the MySQL docs, need to apply an
            # artificial limit if one wasn't provided
            if limit is None:
                limit = 18446744073709551615
            return ' \n LIMIT %s, %s' % (offset, limit)
        else:
            # No offset provided, so just use the limit
            return ' \n LIMIT %s' % (limit,)

    def visit_update(self, update_stmt):
        self.stack.append({'from': set([update_stmt.table])})

        self.isupdate = True
        colparams = self._get_colparams(update_stmt)

        text = "UPDATE " + self.preparer.format_table(update_stmt.table) + \
                " SET " + ', '.join(["%s=%s" % (self.preparer.format_column(c[0]), c[1]) for c in colparams])

        if update_stmt._whereclause:
            text += " WHERE " + self.process(update_stmt._whereclause)

        limit = update_stmt.kwargs.get('mysql_limit', None)
        if limit:
            text += " LIMIT %s" % limit

        self.stack.pop(-1)

        return text

# ug.  "InnoDB needs indexes on foreign keys and referenced keys [...].
#       Starting with MySQL 4.1.2, these indexes are created automatically.
#       In older versions, the indexes must be created explicitly or the
#       creation of foreign key constraints fails."

class MySQLDDLCompiler(compiler.DDLCompiler):
    def get_column_specification(self, column, **kw):
        """Builds column DDL."""

        colspec = [self.preparer.format_column(column),
                    #self.dialect.type_compiler.process(column.type.dialect_impl(self.dialect))
                    self.dialect.type_compiler.process(column.type)
                   ]

        default = self.get_column_default_string(column)
        if default is not None:
            colspec.append('DEFAULT ' + default)

        if not column.nullable:
            colspec.append('NOT NULL')

        if column.primary_key and column.autoincrement:
            try:
                first = [c for c in column.table.primary_key.columns
                         if (c.autoincrement and
                             isinstance(c.type, sqltypes.Integer) and
                             not c.foreign_keys)].pop(0)
                if column is first:
                    colspec.append('AUTO_INCREMENT')
            except IndexError:
                pass

        return ' '.join(colspec)

    def post_create_table(self, table):
        """Build table-level CREATE options like ENGINE and COLLATE."""

        table_opts = []
        for k in table.kwargs:
            if k.startswith('mysql_'):
                opt = k[6:].upper()
                joiner = '='
                if opt in ('TABLESPACE', 'DEFAULT CHARACTER SET',
                           'CHARACTER SET', 'COLLATE'):
                    joiner = ' '

                table_opts.append(joiner.join((opt, table.kwargs[k])))
        return ' '.join(table_opts)

    def visit_drop_index(self, drop):
        index = drop.element
        
        return "\nDROP INDEX %s ON %s" % \
                    (self.preparer.quote(self._validate_identifier(index.name, False), index.quote),
                     self.preparer.format_table(index.table))

    def visit_drop_foreignkey(self, drop):
        constraint = drop.element
        return "ALTER TABLE %s DROP FOREIGN KEY %s" % \
                    (self.preparer.format_table(constraint.table),
                     self.preparer.format_constraint(constraint))

class MySQLTypeCompiler(compiler.GenericTypeCompiler):
    def _extend_numeric(self, type_, spec):
        "Extend a numeric-type declaration with MySQL specific extensions."

        if not self._mysql_type(type_):
            return spec

        if type_.unsigned:
            spec += ' UNSIGNED'
        if type_.zerofill:
            spec += ' ZEROFILL'
        return spec

    def _extend_string(self, type_, spec):
        """Extend a string-type declaration with standard SQL CHARACTER SET /
        COLLATE annotations and MySQL specific extensions.

        """
        if not self._mysql_type(type_):
            return spec
            
        if type_.charset:
            charset = 'CHARACTER SET %s' % type_.charset
        elif type_.ascii:
            charset = 'ASCII'
        elif type_.unicode:
            charset = 'UNICODE'
        else:
            charset = None

        if type_.collation:
            collation = 'COLLATE %s' % type_.collation
        elif type_.binary:
            collation = 'BINARY'
        else:
            collation = None

        if type_.national:
            # NATIONAL (aka NCHAR/NVARCHAR) trumps charsets.
            return ' '.join([c for c in ('NATIONAL', spec, collation)
                             if c is not None])
        return ' '.join([c for c in (spec, charset, collation)
                         if c is not None])
    
    def _mysql_type(self, type_):
        return isinstance(type_, (_StringType, _NumericType, _BinaryType))
    
    def visit_NUMERIC(self, type_):
        if type_.precision is None:
            return self._extend_numeric(type_, "NUMERIC")
        else:
            return self._extend_numeric(type_, "NUMERIC(%(precision)s, %(scale)s)" % {'precision': type_.precision, 'scale' : type_.scale})

    def visit_DECIMAL(self, type_):
        if type_.precision is None:
            return self._extend_numeric(type_, "DECIMAL")
        elif type_.scale is None:
            return self._extend_numeric(type_, "DECIMAL(%(precision)s)" % {'precision': type_.precision})
        else:
            return self._extend_numeric(type_, "DECIMAL(%(precision)s, %(scale)s)" % {'precision': type_.precision, 'scale' : type_.scale})

    def visit_DOUBLE(self, type_):
        if type_.precision is not None and type_.scale is not None:
            return self._extend_numeric(type_, "DOUBLE(%(precision)s, %(scale)s)" %
                                {'precision': type_.precision,
                                 'scale' : type_.scale})
        else:
            return self._extend_numeric(type_, 'DOUBLE')

    def visit_REAL(self, type_):
        if type_.precision is not None and type_.scale is not None:
            return self._extend_numeric(type_, "REAL(%(precision)s, %(scale)s)" %
                                {'precision': type_.precision,
                                 'scale' : type_.scale})
        else:
            return self._extend_numeric(type_, 'REAL')
    
    def visit_FLOAT(self, type_):
        if self._mysql_type(type_) and type_.scale is not None and type_.precision is not None:
            return self._extend_numeric(type_, "FLOAT(%s, %s)" % (type_.precision, type_.scale))
        elif type_.precision is not None:
            return self._extend_numeric(type_, "FLOAT(%s)" % (type_.precision,))
        else:
            return self._extend_numeric(type_, "FLOAT")
    
    def visit_INTEGER(self, type_):
        if self._mysql_type(type_) and type_.display_width is not None:
            return self._extend_numeric(type_, "INTEGER(%(display_width)s)" % {'display_width': type_.display_width})
        else:
            return self._extend_numeric(type_, "INTEGER")
        
    def visit_BIGINT(self, type_):
        if self._mysql_type(type_) and type_.display_width is not None:
            return self._extend_numeric(type_, "BIGINT(%(display_width)s)" % {'display_width': type_.display_width})
        else:
            return self._extend_numeric(type_, "BIGINT")
    
    def visit_MEDIUMINT(self, type_):
        if self._mysql_type(type_) and type_.display_width is not None:
            return self._extend_numeric(type_, "MEDIUMINT(%(display_width)s)" % {'display_width': type_.display_width})
        else:
            return self._extend_numeric(type_, "MEDIUMINT")

    def visit_TINYINT(self, type_):
        if self._mysql_type(type_) and type_.display_width is not None:
            return self._extend_numeric(type_, "TINYINT(%s)" % type_.display_width)
        else:
            return self._extend_numeric(type_, "TINYINT")

    def visit_SMALLINT(self, type_):
        if self._mysql_type(type_) and type_.display_width is not None:
            return self._extend_numeric(type_, "SMALLINT(%(display_width)s)" % {'display_width': type_.display_width})
        else:
            return self._extend_numeric(type_, "SMALLINT")

    def visit_BIT(self, type_):
        if type_.length is not None:
            return "BIT(%s)" % type_.length
        else:
            return "BIT"
    
    def visit_DATETIME(self, type_):
        return "DATETIME"

    def visit_DATE(self, type_):
        return "DATE"

    def visit_TIME(self, type_):
        return "TIME"

    def visit_TIMESTAMP(self, type_):
        return 'TIMESTAMP'

    def visit_YEAR(self, type_):
        if type_.display_width is None:
            return "YEAR"
        else:
            return "YEAR(%s)" % type_.display_width
    
    def visit_TEXT(self, type_):
        if type_.length:
            return self._extend_string(type_, "TEXT(%d)" % type_.length)
        else:
            return self._extend_string(type_, "TEXT")
        
    def visit_TINYTEXT(self, type_):
        return self._extend_string(type_, "TINYTEXT")

    def visit_MEDIUMTEXT(self, type_):
        return self._extend_string(type_, "MEDIUMTEXT")
    
    def visit_LONGTEXT(self, type_):
        return self._extend_string(type_, "LONGTEXT")
    
    def visit_VARCHAR(self, type_):
        if type_.length:
            return self._extend_string(type_, "VARCHAR(%d)" % type_.length)
        else:
            return self._extend_string(type_, "VARCHAR")
    
    def visit_CHAR(self, type_):
        return self._extend_string(type_, "CHAR(%(length)s)" % {'length' : type_.length})

    def visit_NVARCHAR(self, type_):
        # We'll actually generate the equiv. "NATIONAL VARCHAR" instead
        # of "NVARCHAR".
        return self._extend_string(type_, "VARCHAR(%(length)s)" % {'length': type_.length})
    
    def visit_NCHAR(self, type_):
        # We'll actually generate the equiv. "NATIONAL CHAR" instead of "NCHAR".
        return self._extend_string(type_, "CHAR(%(length)s)" % {'length': type_.length})
    
    def visit_VARBINARY(self, type_):
        if type_.length:
            return "VARBINARY(%d)" % type_.length
        else:
            return self.visit_BLOB(type_)
    
    def visit_binary(self, type_):
        return self.visit_BLOB(type_)
        
    def visit_BINARY(self, type_):
        if type_.length:
            return "BINARY(%d)" % type_.length
        else:
            return self.visit_BLOB(type_)
    
    def visit_BLOB(self, type_):
        if type_.length:
            return "BLOB(%d)" % type_.length
        else:
            return "BLOB"
    
    def visit_TINYBLOB(self, type_):
        return "TINYBLOB"

    def visit_MEDIUMBLOB(self, type_):
        return "MEDIUMBLOB"

    def visit_LONGBLOB(self, type_):
        return "LONGBLOB"

    def visit_ENUM(self, type_):
        quoted_enums = []
        for e in type_.enums:
            quoted_enums.append("'%s'" % e.replace("'", "''"))
        return self._extend_string(type_, "ENUM(%s)" % ",".join(quoted_enums))
        
    def visit_SET(self, type_):
        return self._extend_string(type_, "SET(%s)" % ",".join(type_._ddl_values))

    def visit_BOOLEAN(self, type):
        return "BOOL"
        

class MySQLDialect(default.DefaultDialect):
    """Details of the MySQL dialect.  Not used directly in application code."""
    name = 'mysql'
    supports_alter = True
    # identifiers are 64, however aliases can be 255...
    max_identifier_length = 255
    supports_sane_rowcount = True
    default_paramstyle = 'format'

    statement_compiler = MySQLCompiler
    ddl_compiler = MySQLDDLCompiler
    type_compiler = MySQLTypeCompiler
    ischema_names = ischema_names
    
    def __init__(self, use_ansiquotes=None, **kwargs):
        self.use_ansiquotes = use_ansiquotes
        default.DefaultDialect.__init__(self, **kwargs)

    def type_descriptor(self, typeobj):
        return sqltypes.adapt_type(typeobj, colspecs)

    def do_executemany(self, cursor, statement, parameters, context=None):
        rowcount = cursor.executemany(statement, parameters)
        if context is not None:
            context._rowcount = rowcount

    def do_commit(self, connection):
        """Execute a COMMIT."""

        # COMMIT/ROLLBACK were introduced in 3.23.15.
        # Yes, we have at least one user who has to talk to these old versions!
        #
        # Ignore commit/rollback if support isn't present, otherwise even basic
        # operations via autocommit fail.
        try:
            connection.commit()
        except:
            if self._server_version_info(connection) < (3, 23, 15):
                args = sys.exc_info()[1].args
                if args and args[0] == 1064:
                    return
            raise

    def do_rollback(self, connection):
        """Execute a ROLLBACK."""

        try:
            connection.rollback()
        except:
            if self._server_version_info(connection) < (3, 23, 15):
                args = sys.exc_info()[1].args
                if args and args[0] == 1064:
                    return
            raise

    def do_begin_twophase(self, connection, xid):
        connection.execute("XA BEGIN %s", xid)

    def do_prepare_twophase(self, connection, xid):
        connection.execute("XA END %s", xid)
        connection.execute("XA PREPARE %s", xid)

    def do_rollback_twophase(self, connection, xid, is_prepared=True,
                             recover=False):
        if not is_prepared:
            connection.execute("XA END %s", xid)
        connection.execute("XA ROLLBACK %s", xid)

    def do_commit_twophase(self, connection, xid, is_prepared=True,
                           recover=False):
        if not is_prepared:
            self.do_prepare_twophase(connection, xid)
        connection.execute("XA COMMIT %s", xid)

    def do_recover_twophase(self, connection):
        resultset = connection.execute("XA RECOVER")
        return [row['data'][0:row['gtrid_length']] for row in resultset]

    def is_disconnect(self, e):
        if isinstance(e, self.dbapi.OperationalError):
            return self._extract_error_code(e) in (2006, 2013, 2014, 2045, 2055)
        elif isinstance(e, self.dbapi.InterfaceError):  # if underlying connection is closed, this is the error you get
            return "(0, '')" in str(e)
        else:
            return False

    def _compat_fetchall(self, rp, charset=None):
        return rp.fetchall()

    def _compat_fetchone(self, rp, charset=None):
        return rp.fetchone()

    def _extract_error_code(self, exception):
        raise NotImplementedError()
        
    def get_default_schema_name(self, connection):
        return connection.execute('SELECT DATABASE()').scalar()
    get_default_schema_name = engine_base.connection_memoize(
        ('dialect', 'default_schema_name'))(get_default_schema_name)

    def table_names(self, connection, schema):
        """Return a Unicode SHOW TABLES from a given schema."""

        charset = self._detect_charset(connection)
        self._autoset_identifier_style(connection)
        rp = connection.execute("SHOW TABLES FROM %s" %
            self.identifier_preparer.quote_identifier(schema))
        return [row[0] for row in self._compat_fetchall(rp, charset=charset)]

    def has_table(self, connection, table_name, schema=None):
        # SHOW TABLE STATUS LIKE and SHOW TABLES LIKE do not function properly
        # on macosx (and maybe win?) with multibyte table names.
        #
        # TODO: if this is not a problem on win, make the strategy swappable
        # based on platform.  DESCRIBE is slower.

        # [ticket:726]
        # full_name = self.identifier_preparer.format_table(table,
        #                                                   use_schema=True)

        self._autoset_identifier_style(connection)

        full_name = '.'.join(self.identifier_preparer._quote_free_identifiers(
            schema, table_name))

        st = "DESCRIBE %s" % full_name
        rs = None
        try:
            try:
                rs = connection.execute(st)
                have = rs.rowcount > 0
                rs.close()
                return have
            except exc.SQLError, e:
                if self._extract_error_code(e) == 1146:
                    return False
                raise
        finally:
            if rs:
                rs.close()

    @engine_base.connection_memoize(('mysql', 'server_version_info'))
    def server_version_info(self, connection):
        """A tuple of the database server version.

        Formats the remote server version as a tuple of version values,
        e.g. ``(5, 0, 44)``.  If there are strings in the version number
        they will be in the tuple too, so don't count on these all being
        ``int`` values.

        This is a fast check that does not require a round trip.  It is also
        cached per-Connection.
        """

        # TODO: do we need to bypass ConnectionFairy here?  other calls
        # to this seem to not do that.
        return self._server_version_info(connection.connection.connection)

    def reflecttable(self, connection, table, include_columns):
        """Load column definitions from the server."""

        charset = self._detect_charset(connection)
        self._autoset_identifier_style(connection)

        try:
            reflector = self.reflector
        except AttributeError:
            preparer = self.identifier_preparer
            if (self.server_version_info(connection) < (4, 1) and
                self.use_ansiquotes):
                # ANSI_QUOTES doesn't affect SHOW CREATE TABLE on < 4.1
                preparer = MySQLIdentifierPreparer(self)

            self.reflector = reflector = MySQLSchemaReflector(self, preparer)

        sql = self._show_create_table(connection, table, charset)
        if sql.startswith('CREATE ALGORITHM'):
            # Adapt views to something table-like.
            columns = self._describe_table(connection, table, charset)
            sql = reflector._describe_to_create(table, columns)

        self._adjust_casing(connection, table)

        return reflector.reflect(connection, table, sql, charset,
                                 only=include_columns)

    def _adjust_casing(self, connection, table, charset=None):
        """Adjust Table name to the server case sensitivity, if needed."""

        casing = self._detect_casing(connection)

        # For winxx database hosts.  TODO: is this really needed?
        if casing == 1 and table.name != table.name.lower():
            table.name = table.name.lower()
            lc_alias = schema._get_table_key(table.name, table.schema)
            table.metadata.tables[lc_alias] = table

    def _detect_charset(self, connection):
        """Sniff out the character set in use for connection results."""

        # Allow user override, won't sniff if force_charset is set.
        if ('mysql', 'force_charset') in connection.info:
            return connection.info[('mysql', 'force_charset')]

        # Prefer 'character_set_results' for the current connection over the
        # value in the driver.  SET NAMES or individual variable SETs will
        # change the charset without updating the driver's view of the world.
        #
        # If it's decided that issuing that sort of SQL leaves you SOL, then
        # this can prefer the driver value.
        rs = connection.execute("SHOW VARIABLES LIKE 'character_set%%'")
        opts = dict([(row[0], row[1]) for row in self._compat_fetchall(rs)])

        if 'character_set_results' in opts:
            return opts['character_set_results']
        # Still no charset on < 1.2.1 final...
        if 'character_set' in opts:
            return opts['character_set']
        else:
            util.warn(
                "Could not detect the connection character set.  Assuming latin1.")
            return 'latin1'
    _detect_charset = engine_base.connection_memoize(
        ('mysql', 'charset'))(_detect_charset)


    def _detect_casing(self, connection):
        """Sniff out identifier case sensitivity.

        Cached per-connection. This value can not change without a server
        restart.

        """
        # http://dev.mysql.com/doc/refman/5.0/en/name-case-sensitivity.html

        charset = self._detect_charset(connection)
        row = self._compat_fetchone(connection.execute(
            "SHOW VARIABLES LIKE 'lower_case_table_names'"),
                               charset=charset)
        if not row:
            cs = 0
        else:
            # 4.0.15 returns OFF or ON according to [ticket:489]
            # 3.23 doesn't, 4.0.27 doesn't..
            if row[1] == 'OFF':
                cs = 0
            elif row[1] == 'ON':
                cs = 1
            else:
                cs = int(row[1])
            row.close()
        return cs
    _detect_casing = engine_base.connection_memoize(
        ('mysql', 'lower_case_table_names'))(_detect_casing)

    def _detect_collations(self, connection):
        """Pull the active COLLATIONS list from the server.

        Cached per-connection.
        """

        collations = {}
        if self.server_version_info(connection) < (4, 1, 0):
            pass
        else:
            charset = self._detect_charset(connection)
            rs = connection.execute('SHOW COLLATION')
            for row in self._compat_fetchall(rs, charset):
                collations[row[0]] = row[1]
        return collations
    _detect_collations = engine_base.connection_memoize(
        ('mysql', 'collations'))(_detect_collations)

    def use_ansiquotes(self, useansi):
        self._use_ansiquotes = useansi
        if useansi:
            self.preparer = MySQLANSIIdentifierPreparer
        else:
            self.preparer = MySQLIdentifierPreparer
        # icky
        if hasattr(self, 'identifier_preparer'):
            self.identifier_preparer = self.preparer(self)
        if hasattr(self, 'reflector'):
            del self.reflector

    use_ansiquotes = property(lambda s: s._use_ansiquotes, use_ansiquotes,
                              doc="True if ANSI_QUOTES is in effect.")

    def _autoset_identifier_style(self, connection, charset=None):
        """Detect and adjust for the ANSI_QUOTES sql mode.

        If the dialect's use_ansiquotes is unset, query the server's sql mode
        and reset the identifier style.

        Note that this currently *only* runs during reflection.  Ideally this
        would run the first time a connection pool connects to the database,
        but the infrastructure for that is not yet in place.
        """

        if self.use_ansiquotes is not None:
            return

        row = self._compat_fetchone(
            connection.execute("SHOW VARIABLES LIKE 'sql_mode'"),
                               charset=charset)
        if not row:
            mode = ''
        else:
            mode = row[1] or ''
            # 4.0
            if mode.isdigit():
                mode_no = int(mode)
                mode = (mode_no | 4 == mode_no) and 'ANSI_QUOTES' or ''

        self.use_ansiquotes = 'ANSI_QUOTES' in mode

    def _show_create_table(self, connection, table, charset=None,
                           full_name=None):
        """Run SHOW CREATE TABLE for a ``Table``."""

        if full_name is None:
            full_name = self.identifier_preparer.format_table(table)
        st = "SHOW CREATE TABLE %s" % full_name

        rp = None
        try:
            try:
                rp = connection.execute(st)
            except exc.SQLError, e:
                if e.orig.args[0] == 1146:
                    raise exc.NoSuchTableError(full_name)
                else:
                    raise
            row = self._compat_fetchone(rp, charset=charset)
            if not row:
                raise exc.NoSuchTableError(full_name)
            return row[1].strip()
        finally:
            if rp:
                rp.close()

        return sql

    def _describe_table(self, connection, table, charset=None,
                             full_name=None):
        """Run DESCRIBE for a ``Table`` and return processed rows."""

        if full_name is None:
            full_name = self.identifier_preparer.format_table(table)
        st = "DESCRIBE %s" % full_name

        rp, rows = None, None
        try:
            try:
                rp = connection.execute(st)
            except exc.SQLError, e:
                if e.orig.args[0] == 1146:
                    raise exc.NoSuchTableError(full_name)
                else:
                    raise
            rows = self._compat_fetchall(rp, charset=charset)
        finally:
            if rp:
                rp.close()
        return rows


class MySQLSchemaReflector(object):
    """Parses SHOW CREATE TABLE output."""

    def __init__(self, dialect, preparer=None):
        """Construct a MySQLSchemaReflector.

        identifier_preparer
          An ANSIIdentifierPreparer type, used to determine the identifier
          quoting style in effect.
        """

        self.dialect = dialect
        self.preparer = preparer or dialect.identifier_preparer
        self._prep_regexes()

    def reflect(self, connection, table, show_create, charset, only=None):
        """Parse MySQL SHOW CREATE TABLE and fill in a ''Table''.

        show_create
          Unicode output of SHOW CREATE TABLE

        table
          A ''Table'', to be loaded with Columns, Indexes, etc.
          table.name will be set if not already

        charset
          FIXME, some constructed values (like column defaults)
          currently can't be Unicode.  ''charset'' will convert them
          into the connection character set.

        only
           An optional sequence of column names.  If provided, only
           these columns will be reflected, and any keys or constraints
           that include columns outside this set will also be omitted.
           That means that if ``only`` includes only one column in a
           2 part primary key, the entire primary key will be omitted.
        """

        keys, constraints = [], []

        if only:
            only = set(only)

        for line in re.split(r'\r?\n', show_create):
            if line.startswith('  ' + self.preparer.initial_quote):
                self._add_column(table, line, charset, only)
            # a regular table options line
            elif line.startswith(') '):
                self._set_options(table, line)
            # an ANSI-mode table options line
            elif line == ')':
                pass
            elif line.startswith('CREATE '):
                self._set_name(table, line)
            # Not present in real reflection, but may be if loading from a file.
            elif not line:
                pass
            else:
                type_, spec = self.parse_constraints(line)
                if type_ is None:
                    util.warn("Unknown schema content: %r" % line)
                elif type_ == 'key':
                    keys.append(spec)
                elif type_ == 'constraint':
                    constraints.append(spec)
                else:
                    pass

        self._set_keys(table, keys, only)
        self._set_constraints(table, constraints, connection, only)

    def _set_name(self, table, line):
        """Override a Table name with the reflected name.

        table
          A ``Table``

        line
          The first line of SHOW CREATE TABLE output.
        """

        # Don't override by default.
        if table.name is None:
            table.name = self.parse_name(line)

    def _add_column(self, table, line, charset, only=None):
        spec = self.parse_column(line)
        if not spec:
            util.warn("Unknown column definition %r" % line)
            return
        if not spec['full']:
            util.warn("Incomplete reflection of column definition %r" % line)

        name, type_, args, notnull = \
              spec['name'], spec['coltype'], spec['arg'], spec['notnull']

        if only and name not in only:
            self.logger.info("Omitting reflected column %s.%s" %
                             (table.name, name))
            return

        # Convention says that TINYINT(1) columns == BOOLEAN
        if type_ == 'tinyint' and args == '1':
            type_ = 'boolean'
            args = None

        try:
            col_type = self.dialect.ischema_names[type_]
        except KeyError:
            util.warn("Did not recognize type '%s' of column '%s'" %
                      (type_, name))
            col_type = sqltypes.NullType

        # Column type positional arguments eg. varchar(32)
        if args is None or args == '':
            type_args = []
        elif args[0] == "'" and args[-1] == "'":
            type_args = self._re_csv_str.findall(args)
        else:
            type_args = [int(v) for v in self._re_csv_int.findall(args)]

        # Column type keyword options
        type_kw = {}
        for kw in ('unsigned', 'zerofill'):
            if spec.get(kw, False):
                type_kw[kw] = True
        for kw in ('charset', 'collate'):
            if spec.get(kw, False):
                type_kw[kw] = spec[kw]

        if type_ == 'enum':
            type_kw['quoting'] = 'quoted'

        type_instance = col_type(*type_args, **type_kw)

        col_args, col_kw = [], {}

        # NOT NULL
        if spec.get('notnull', False):
            col_kw['nullable'] = False

        # AUTO_INCREMENT
        if spec.get('autoincr', False):
            col_kw['autoincrement'] = True
        elif issubclass(col_type, sqltypes.Integer):
            col_kw['autoincrement'] = False

        # DEFAULT
        default = spec.get('default', None)
        if default is not None and default != 'NULL':
            # Defaults should be in the native charset for the moment
            default = default.encode(charset)
            if type_ == 'timestamp':
                # can't be NULL for TIMESTAMPs
                if (default[0], default[-1]) != ("'", "'"):
                    default = sql.text(default)
            else:
                default = default[1:-1]
            col_args.append(schema.DefaultClause(default))

        table.append_column(schema.Column(name, type_instance,
                                          *col_args, **col_kw))

    def _set_keys(self, table, keys, only):
        """Add ``Index`` and ``PrimaryKeyConstraint`` items to a ``Table``.

        Most of the information gets dropped here- more is reflected than
        the schema objects can currently represent.

        table
          A ``Table``

        keys
          A sequence of key specifications produced by `constraints`

        only
          Optional `set` of column names.  If provided, keys covering
          columns not in this set will be omitted.
        """

        for spec in keys:
            flavor = spec['type']
            col_names = [s[0] for s in spec['columns']]

            if only and not set(col_names).issubset(only):
                if flavor is None:
                    flavor = 'index'
                self.logger.info(
                    "Omitting %s KEY for (%s), key covers ommitted columns." %
                    (flavor, ', '.join(col_names)))
                continue

            constraint = False
            if flavor == 'PRIMARY':
                key = schema.PrimaryKeyConstraint()
                constraint = True
            elif flavor == 'UNIQUE':
                key = schema.Index(spec['name'], unique=True)
            elif flavor in (None, 'FULLTEXT', 'SPATIAL'):
                key = schema.Index(spec['name'])
            else:
                self.logger.info(
                    "Converting unknown KEY type %s to a plain KEY" % flavor)
                key = schema.Index(spec['name'])

            for col in [table.c[name] for name in col_names]:
                key.append_column(col)

            if constraint:
                table.append_constraint(key)

    def _set_constraints(self, table, constraints, connection, only):
        """Apply constraints to a ``Table``."""

        default_schema = None

        for spec in constraints:
            # only FOREIGN KEYs
            ref_name = spec['table'][-1]
            ref_schema = len(spec['table']) > 1 and spec['table'][-2] or table.schema

            if not ref_schema:
                if default_schema is None:
                    default_schema = connection.dialect.get_default_schema_name(
                        connection)
                if table.schema == default_schema:
                    ref_schema = table.schema

            loc_names = spec['local']
            if only and not set(loc_names).issubset(only):
                self.logger.info(
                    "Omitting FOREIGN KEY for (%s), key covers ommitted "
                    "columns." % (', '.join(loc_names)))
                continue

            ref_key = schema._get_table_key(ref_name, ref_schema)
            if ref_key in table.metadata.tables:
                ref_table = table.metadata.tables[ref_key]
            else:
                ref_table = schema.Table(
                    ref_name, table.metadata, schema=ref_schema,
                    autoload=True, autoload_with=connection)

            ref_names = spec['foreign']

            if ref_schema:
                refspec = [".".join([ref_schema, ref_name, column]) for column in ref_names]
            else:
                refspec = [".".join([ref_name, column]) for column in ref_names]

            con_kw = {}
            for opt in ('name', 'onupdate', 'ondelete'):
                if spec.get(opt, False):
                    con_kw[opt] = spec[opt]

            key = schema.ForeignKeyConstraint(loc_names, refspec, link_to_name=True, **con_kw)
            table.append_constraint(key)

    def _set_options(self, table, line):
        """Apply safe reflected table options to a ``Table``.

        table
          A ``Table``

        line
          The final line of SHOW CREATE TABLE output.
        """

        options = self.parse_table_options(line)
        for nope in ('auto_increment', 'data_directory', 'index_directory'):
            options.pop(nope, None)

        for opt, val in options.items():
            table.kwargs['mysql_%s' % opt] = val

    def _prep_regexes(self):
        """Pre-compile regular expressions."""

        self._re_columns = []
        self._pr_options = []
        self._re_options_util = {}

        _final = self.preparer.final_quote

        quotes = dict(zip(('iq', 'fq', 'esc_fq'),
                          [re.escape(s) for s in
                           (self.preparer.initial_quote,
                            _final,
                            self.preparer._escape_identifier(_final))]))

        self._pr_name = _pr_compile(
            r'^CREATE (?:\w+ +)?TABLE +'
            r'%(iq)s(?P<name>(?:%(esc_fq)s|[^%(fq)s])+)%(fq)s +\($' % quotes,
            self.preparer._unescape_identifier)

        # `col`,`col2`(32),`col3`(15) DESC
        #
        # Note: ASC and DESC aren't reflected, so we'll punt...
        self._re_keyexprs = _re_compile(
            r'(?:'
            r'(?:%(iq)s((?:%(esc_fq)s|[^%(fq)s])+)%(fq)s)'
            r'(?:\((\d+)\))?(?=\,|$))+' % quotes)

        # 'foo' or 'foo','bar' or 'fo,o','ba''a''r'
        self._re_csv_str = _re_compile(r'\x27(?:\x27\x27|[^\x27])*\x27')

        # 123 or 123,456
        self._re_csv_int = _re_compile(r'\d+')


        # `colname` <type> [type opts]
        #  (NOT NULL | NULL)
        #   DEFAULT ('value' | CURRENT_TIMESTAMP...)
        #   COMMENT 'comment'
        #  COLUMN_FORMAT (FIXED|DYNAMIC|DEFAULT)
        #  STORAGE (DISK|MEMORY)
        self._re_column = _re_compile(
            r'  '
            r'%(iq)s(?P<name>(?:%(esc_fq)s|[^%(fq)s])+)%(fq)s +'
            r'(?P<coltype>\w+)'
            r'(?:\((?P<arg>(?:\d+|\d+,\d+|'
              r'(?:\x27(?:\x27\x27|[^\x27])*\x27,?)+))\))?'
            r'(?: +(?P<unsigned>UNSIGNED))?'
            r'(?: +(?P<zerofill>ZEROFILL))?'
            r'(?: +CHARACTER SET +(?P<charset>\w+))?'
            r'(?: +COLLATE +(P<collate>\w+))?'
            r'(?: +(?P<notnull>NOT NULL))?'
            r'(?: +DEFAULT +(?P<default>'
              r'(?:NULL|\x27(?:\x27\x27|[^\x27])*\x27|\w+)'
              r'(?:ON UPDATE \w+)?'
            r'))?'
            r'(?: +(?P<autoincr>AUTO_INCREMENT))?'
            r'(?: +COMMENT +(P<comment>(?:\x27\x27|[^\x27])+))?'
            r'(?: +COLUMN_FORMAT +(?P<colfmt>\w+))?'
            r'(?: +STORAGE +(?P<storage>\w+))?'
            r'(?: +(?P<extra>.*))?'
            r',?$'
            % quotes
            )

        # Fallback, try to parse as little as possible
        self._re_column_loose = _re_compile(
            r'  '
            r'%(iq)s(?P<name>(?:%(esc_fq)s|[^%(fq)s])+)%(fq)s +'
            r'(?P<coltype>\w+)'
            r'(?:\((?P<arg>(?:\d+|\d+,\d+|\x27(?:\x27\x27|[^\x27])+\x27))\))?'
            r'.*?(?P<notnull>NOT NULL)?'
            % quotes
            )

        # (PRIMARY|UNIQUE|FULLTEXT|SPATIAL) INDEX `name` (USING (BTREE|HASH))?
        # (`col` (ASC|DESC)?, `col` (ASC|DESC)?)
        # KEY_BLOCK_SIZE size | WITH PARSER name
        self._re_key = _re_compile(
            r'  '
            r'(?:(?P<type>\S+) )?KEY'
            r'(?: +%(iq)s(?P<name>(?:%(esc_fq)s|[^%(fq)s])+)%(fq)s)?'
            r'(?: +USING +(?P<using_pre>\S+))?'
            r' +\((?P<columns>.+?)\)'
            r'(?: +USING +(?P<using_post>\S+))?'
            r'(?: +KEY_BLOCK_SIZE +(?P<keyblock>\S+))?'
            r'(?: +WITH PARSER +(?P<parser>\S+))?'
            r',?$'
            % quotes
            )

        # CONSTRAINT `name` FOREIGN KEY (`local_col`)
        # REFERENCES `remote` (`remote_col`)
        # MATCH FULL | MATCH PARTIAL | MATCH SIMPLE
        # ON DELETE CASCADE ON UPDATE RESTRICT
        #
        # unique constraints come back as KEYs
        kw = quotes.copy()
        kw['on'] = 'RESTRICT|CASCASDE|SET NULL|NOACTION'
        self._re_constraint = _re_compile(
            r'  '
            r'CONSTRAINT +'
            r'%(iq)s(?P<name>(?:%(esc_fq)s|[^%(fq)s])+)%(fq)s +'
            r'FOREIGN KEY +'
            r'\((?P<local>[^\)]+?)\) REFERENCES +'
            r'(?P<table>%(iq)s[^%(fq)s]+%(fq)s) +'
            r'\((?P<foreign>[^\)]+?)\)'
            r'(?: +(?P<match>MATCH \w+))?'
            r'(?: +ON DELETE (?P<ondelete>%(on)s))?'
            r'(?: +ON UPDATE (?P<onupdate>%(on)s))?'
            % kw
            )

        # PARTITION
        #
        # punt!
        self._re_partition = _re_compile(
            r'  '
            r'(?:SUB)?PARTITION')

        # Table-level options (COLLATE, ENGINE, etc.)
        for option in ('ENGINE', 'TYPE', 'AUTO_INCREMENT',
                       'AVG_ROW_LENGTH', 'CHARACTER SET',
                       'DEFAULT CHARSET', 'CHECKSUM',
                       'COLLATE', 'DELAY_KEY_WRITE', 'INSERT_METHOD',
                       'MAX_ROWS', 'MIN_ROWS', 'PACK_KEYS', 'ROW_FORMAT',
                       'KEY_BLOCK_SIZE'):
            self._add_option_word(option)

        for option in (('COMMENT', 'DATA_DIRECTORY', 'INDEX_DIRECTORY',
                        'PASSWORD', 'CONNECTION')):
            self._add_option_string(option)

        self._add_option_regex('UNION', r'\([^\)]+\)')
        self._add_option_regex('TABLESPACE', r'.*? STORAGE DISK')
        self._add_option_regex('RAID_TYPE',
          r'\w+\s+RAID_CHUNKS\s*\=\s*\w+RAID_CHUNKSIZE\s*=\s*\w+')
        self._re_options_util['='] = _re_compile(r'\s*=\s*$')

    def _add_option_string(self, directive):
        regex = (r'(?P<directive>%s\s*(?:=\s*)?)'
                 r'(?:\x27.(?P<val>.*?)\x27(?!\x27)\x27)' %
                 re.escape(directive))
        self._pr_options.append(
            _pr_compile(regex, lambda v: v.replace("''", "'")))

    def _add_option_word(self, directive):
        regex = (r'(?P<directive>%s\s*(?:=\s*)?)'
                 r'(?P<val>\w+)' % re.escape(directive))
        self._pr_options.append(_pr_compile(regex))

    def _add_option_regex(self, directive, regex):
        regex = (r'(?P<directive>%s\s*(?:=\s*)?)'
                 r'(?P<val>%s)' % (re.escape(directive), regex))
        self._pr_options.append(_pr_compile(regex))


    def parse_name(self, line):
        """Extract the table name.

        line
          The first line of SHOW CREATE TABLE
        """

        regex, cleanup = self._pr_name
        m = regex.match(line)
        if not m:
            return None
        return cleanup(m.group('name'))

    def parse_column(self, line):
        """Extract column details.

        Falls back to a 'minimal support' variant if full parse fails.

        line
          Any column-bearing line from SHOW CREATE TABLE
        """

        m = self._re_column.match(line)
        if m:
            spec = m.groupdict()
            spec['full'] = True
            return spec
        m = self._re_column_loose.match(line)
        if m:
            spec = m.groupdict()
            spec['full'] = False
            return spec
        return None

    def parse_constraints(self, line):
        """Parse a KEY or CONSTRAINT line.

        line
          A line of SHOW CREATE TABLE output
        """

        # KEY
        m = self._re_key.match(line)
        if m:
            spec = m.groupdict()
            # convert columns into name, length pairs
            spec['columns'] = self._parse_keyexprs(spec['columns'])
            return 'key', spec

        # CONSTRAINT
        m = self._re_constraint.match(line)
        if m:
            spec = m.groupdict()
            spec['table'] = \
              self.preparer.unformat_identifiers(spec['table'])
            spec['local'] = [c[0]
                             for c in self._parse_keyexprs(spec['local'])]
            spec['foreign'] = [c[0]
                               for c in self._parse_keyexprs(spec['foreign'])]
            return 'constraint', spec

        # PARTITION and SUBPARTITION
        m = self._re_partition.match(line)
        if m:
            # Punt!
            return 'partition', line

        # No match.
        return (None, line)

    def parse_table_options(self, line):
        """Build a dictionary of all reflected table-level options.

        line
          The final line of SHOW CREATE TABLE output.
        """

        options = {}

        if not line or line == ')':
            return options

        r_eq_trim = self._re_options_util['=']

        for regex, cleanup in self._pr_options:
            m = regex.search(line)
            if not m:
                continue
            directive, value = m.group('directive'), m.group('val')
            directive = r_eq_trim.sub('', directive).lower()
            if cleanup:
                value = cleanup(value)
            options[directive] = value

        return options

    def _describe_to_create(self, table, columns):
        """Re-format DESCRIBE output as a SHOW CREATE TABLE string.

        DESCRIBE is a much simpler reflection and is sufficient for
        reflecting views for runtime use.  This method formats DDL
        for columns only- keys are omitted.

        `columns` is a sequence of DESCRIBE or SHOW COLUMNS 6-tuples.
        SHOW FULL COLUMNS FROM rows must be rearranged for use with
        this function.
        """

        buffer = []
        for row in columns:
            (name, col_type, nullable, default, extra) = \
                   [row[i] for i in (0, 1, 2, 4, 5)]

            line = [' ']
            line.append(self.preparer.quote_identifier(name))
            line.append(col_type)
            if not nullable:
                line.append('NOT NULL')
            if default:
                if 'auto_increment' in default:
                    pass
                elif (col_type.startswith('timestamp') and
                      default.startswith('C')):
                    line.append('DEFAULT')
                    line.append(default)
                elif default == 'NULL':
                    line.append('DEFAULT')
                    line.append(default)
                else:
                    line.append('DEFAULT')
                    line.append("'%s'" % default.replace("'", "''"))
            if extra:
                line.append(extra)

            buffer.append(' '.join(line))

        return ''.join([('CREATE TABLE %s (\n' %
                         self.preparer.quote_identifier(table.name)),
                        ',\n'.join(buffer),
                        '\n) '])

    def _parse_keyexprs(self, identifiers):
        """Unpack '"col"(2),"col" ASC'-ish strings into components."""

        return self._re_keyexprs.findall(identifiers)

log.class_logger(MySQLSchemaReflector)


class _MySQLIdentifierPreparer(compiler.IdentifierPreparer):
    """MySQL-specific schema identifier configuration."""

    reserved_words = RESERVED_WORDS

    def __init__(self, dialect, **kw):
        super(_MySQLIdentifierPreparer, self).__init__(dialect, **kw)

    def _quote_free_identifiers(self, *ids):
        """Unilaterally identifier-quote any number of strings."""

        return tuple([self.quote_identifier(i) for i in ids if i is not None])


class MySQLIdentifierPreparer(_MySQLIdentifierPreparer):
    """Traditional MySQL-specific schema identifier configuration."""

    def __init__(self, dialect):
        super(MySQLIdentifierPreparer, self).__init__(dialect, initial_quote="`")

    def _escape_identifier(self, value):
        return value.replace('`', '``')

    def _unescape_identifier(self, value):
        return value.replace('``', '`')


class MySQLANSIIdentifierPreparer(_MySQLIdentifierPreparer):
    """ANSI_QUOTES MySQL schema identifier configuration."""

    pass

def _pr_compile(regex, cleanup=None):
    """Prepare a 2-tuple of compiled regex and callable."""

    return (_re_compile(regex), cleanup)

def _re_compile(regex):
    """Compile a string to regex, I and UNICODE."""

    return re.compile(regex, re.I | re.UNICODE)

