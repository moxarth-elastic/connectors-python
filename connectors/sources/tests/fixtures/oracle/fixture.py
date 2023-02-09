#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""Oracle module responsible to generate records on the Oracle server.
"""
import os
import random
import string
import time

import oracledb

DATA_SIZE = os.environ.get("DATA_SIZE", "small").lower()
_SIZES = {"small": 5, "medium": 10, "large": 30}
NUM_TABLES = _SIZES[DATA_SIZE]
RETRY_INTERVAL = 5
RETRIES = 5


def random_text(k=1024 * 20):
    """Generate random string for the record content

    Args:
        k (int): Length to generate the string
    """
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=k))


BIG_TEXT = random_text()
DSN = "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=127.0.0.1)(PORT=9090))(CONNECT_DATA=(SID=xe)))"


def inject_lines(table, cursor, start, lines):
    """Ingest rows in table

    Args:
        table (str): Name of table
        cursor (cursor): Cursor to execute query
        start (int): Starting row
        lines (int): Number of rows
    """
    rows = []
    for row_id in range(lines):
        row_id += start
        rows.append((row_id + 1, f"user_{row_id}", row_id, BIG_TEXT))
    sql_query = f"INSERT into customers_{table} VALUES (:1, :2, :3, :4)"
    cursor.executemany(sql_query, rows)


def load():
    """N tables of 10001 rows each. each row is ~ 1024*20 bytes"""
    retry = 1
    while retry <= RETRIES:
        try:
            connection = oracledb.connect(
                user="system", password="Password_123", dsn=DSN, encoding="UTF-8"
            )
            cursor = connection.cursor()
            cursor.execute("CREATE USER admin IDENTIFIED by Password_123")
            cursor.execute("GRANT CONNECT, RESOURCE, DBA TO admin")
            connection.commit()

            connection = oracledb.connect(
                user="admin", password="Password_123", dsn=DSN, encoding="UTF-8"
            )
            cursor = connection.cursor()
            for table in range(NUM_TABLES):
                print(f"Adding data in {table}...")
                sql_query = f"CREATE TABLE customers_{table} (id int, name VARCHAR(255), age int, description long, PRIMARY KEY (id))"
                cursor.execute(sql_query)
                for i in range(10):
                    inject_lines(table, cursor, i * 1000, 1000)
            connection.commit()
            break
        except Exception as exception:
            print(f"Retry count: {retry} out of {RETRIES}. Exception: {exception}")
            if retry == RETRIES:
                raise Exception(
                    "Docker is taking too much time to start the Oracle service. Please rerun the ftest."
                )
            time.sleep(RETRY_INTERVAL**retry)
            retry += 1


def remove():
    """Removes 10 random items per table"""
    connection = oracledb.connect(
        user="admin", password="Password_123", dsn=DSN, encoding="UTF-8"
    )
    cursor = connection.cursor()

    for table in range(NUM_TABLES):
        rows = [(f"user_{row_id}",) for row_id in random.sample(range(1, 1000), 10)]
        sql_query = f"DELETE from customers_{table} where name=:1"
        cursor.executemany(sql_query, rows)
    connection.commit()
