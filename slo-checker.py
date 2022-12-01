#!/usr/bin/env python3

import sys
import logging
from typing import Callable
import requests
import signal
import time
from datetime import datetime

from environs import Env
import mysql.connector
import operator

env = Env()
env.read_env()


class Config(object):

    prometheus_api_url = env("PROMETHEUS_API_URL")
    scrape_interval = env.int("SCRAPE_INTERVAL", 60)
    log_level = env.log_level("LOG_LEVEL", logging.INFO)

    mysql_host = env("MYSQL_HOST", 'localhost')
    mysql_port = env.int("MYSQL_PORT", '3306')
    mysql_user = env("MYSQL_USER", 'root')
    mysql_password = env("MYSQL_PASS", '1234')
    mysql_db_name = env("MYSQL_DB_NAME", 'sla')
    mock_db = env.bool("MOCK_DB", False)


class Mysql:
    def __init__(self, config: Config) -> None:
        logging.info('Connecting db')

        self.connection = mysql.connector.connect(host=config.mysql_host, user=config.mysql_user,
                                                  passwd=config.mysql_password, auth_plugin='mysql_native_password')
        self.table_name = 'indicators'

        logging.info('Starting migration')

        cursor = self.connection.cursor()
        cursor.execute('CREATE DATABASE IF NOT EXISTS %s' %
                       (config.mysql_db_name))

        cursor.execute('USE sla')

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS %s(
                datetime datetime not null default NOW(),
                name varchar(255) not null,
                slo float(4) not null,
                value float(4) not null,
                is_bad bool not null default false
            )
        """ % (self.table_name))
        cursor.execute("""
            ALTER TABLE %s ADD INDEX (datetime)
        """ % (self.table_name))
        cursor.execute("""
            ALTER TABLE %s ADD INDEX (name)
        """ % (self.table_name))

    def save_indicator(self, name, slo, value, is_bad=False, time=None):
        cursor = self.connection.cursor()
        sql = f"INSERT INTO {self.table_name} (name, slo, value, is_bad, datetime) VALUES (%s, %s, %s, %s, %s)"
        val = (name, slo, value, int(is_bad), time)
        cursor.execute(sql, val)
        self.connection.commit()


class MysqlMock:
    def __init__(self, config: Config) -> None:
        pass

    def save_indicator(self, name, slo, value, is_bad=False, time=None):
        logging.debug(
            f"name={name}, slo={slo}, value={value}, is_bad={is_bad}, time={time}")


class PrometheusRequest:
    def __init__(self, config: Config) -> None:
        self.prometheus_api_url = config.prometheus_api_url

    def lastValue(self, query, time, default):
        try:
            response = requests.get(
                self.prometheus_api_url + '/api/v1/query', params={'query': query, 'time': time})

            content = response.json()
            if not content:
                return default

            if len(content['data']['result']) == 0:
                return default

            return content['data']['result'][0]['value'][1]
        except Exception as error:
            logging.error(error)
            return default


def setup_logging(config: Config):
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%H:%M:%S',
        level=config.log_level)


class Indicator:
    def record(self, timestamp: float):
        raise NotImplementedError


class FallibleActionIndicator(Indicator):
    def __init__(self,
                 db: Mysql,
                 prom: PrometheusRequest,
                 value_name: str,
                 slo: int,
                 is_bad_cond: Callable[[int, int], bool],
                 pretty_name: str = None,
                 missing_value=0) -> None:
        self.db = db
        self.prom = prom
        self.value_name = value_name
        self.slo = slo
        self._is_bad = is_bad_cond
        self.missing_value = missing_value
        self.pretty_name = pretty_name or value_name

    def record(self, timestamp: float):
        unixtimestamp = int(timestamp)
        date_format = datetime.utcfromtimestamp(
            unixtimestamp).strftime('%Y-%m-%d %H:%M:%S')
        value = self.prom.lastValue(
            self.value_name,
            unixtimestamp, self.missing_value
        )
        value = int(float(value))
        self.db.save_indicator(
            name=self.pretty_name,
            slo=self.slo,
            value=value,
            is_bad=self._is_bad(value, self.slo),
            time=date_format)


class TimeLimitIndicator(Indicator):
    def __init__(self,
                 db: Mysql,
                 prom: PrometheusRequest,
                 value_name: str,
                 limit: int,
                 pretty_name: str = None, missing_value: int = 2000) -> None:
        self.db = db
        self.prom = prom
        self.value_name = value_name
        self.limit = limit
        self.pretty_name = pretty_name or self.value_name
        self.missing_value = missing_value

    def record(self, timestamp: float):
        unixtimestamp = int(timestamp)
        date_format = datetime.utcfromtimestamp(
            unixtimestamp).strftime('%Y-%m-%d %H:%M:%S')
        value = self.prom.lastValue(
            self.value_name,
            unixtimestamp, self.missing_value
        )
        value = float(value)
        self.db.save_indicator(
            name=self.pretty_name,
            slo=self.limit,
            value=int(value),
            is_bad=value > self.limit,
            time=date_format)


def main():

    config = Config()
    setup_logging(config)
    # you may want to use MysqlMock for debugging
    if config.mock_db:
        db = MysqlMock(config)
    else:
        db = Mysql(config)
    prom = PrometheusRequest(config)

    logging.info(f"Starting sla checker")

    indicators = [

        FallibleActionIndicator(
            db, prom, 'increase(prober_load_frontpage_scenario_total{status="fail"}[1m])', 0, operator.gt, pretty_name="prober_load_frontpage_scenario_success", missing_value=0),
        TimeLimitIndicator(
            db, prom, 'prober_load_frontpage_scenario_duration_milliseconds', 2000, missing_value=10000
        ),

        FallibleActionIndicator(
            db, prom, 'increase(prober_create_user_scenario_total{status="fail"}[1m])', 0, operator.gt, missing_value=0,
            pretty_name="prober_create_user_scenario_success"),
        TimeLimitIndicator(
            db, prom, "prober_create_user_scenario_duration_milliseconds", 100),

        FallibleActionIndicator(
            db, prom, 'increase(prober_create_team_scenario_total{status="fail"}[1m])', 0, operator.gt, missing_value=0, pretty_name="prober_create_team_scenario_success"),
        TimeLimitIndicator(
            db, prom, "prober_create_team_scenario_duration_milliseconds", 200),

        FallibleActionIndicator(
            db, prom, 'increase(prober_create_event_scenario_total{status="fail"}[1m])', 0, operator.gt, missing_value=0, pretty_name="prober_create_event_scenario_success"),
        TimeLimitIndicator(
            db, prom, "prober_create_event_scenario_duration_milliseconds", 100),
    ]

    while True:
        logging.debug(f"Run prober")

        timestamp = time.time()

        for indicator in indicators:
            indicator.record(timestamp)

        logging.debug(
            f"Waiting {config.scrape_interval} seconds for next loop")
        time.sleep(config.scrape_interval)


def terminate(signal, frame):
    print("Terminating")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    main()
