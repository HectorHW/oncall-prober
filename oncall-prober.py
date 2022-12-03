#!/usr/bin/env python3
import logging
import signal
import sys
import time
from typing import List
from environs import Env
import prometheus_client
from prometheus_client import start_http_server, Counter, Gauge
import requests
import posixpath
import datetime
from selenium import webdriver
from selenium.webdriver.chromium.options import ChromiumOptions

prometheus_client.REGISTRY.unregister(prometheus_client.GC_COLLECTOR)
prometheus_client.REGISTRY.unregister(prometheus_client.PLATFORM_COLLECTOR)
prometheus_client.REGISTRY.unregister(prometheus_client.PROCESS_COLLECTOR)

env = Env()
env.read_env()


class Config:
    oncall_prober_base_url = env("ONCALL_PROBER_BASE_URL")
    oncall_prober_api_url = env(
        "ONCALL_PROBER_API_URL",
        posixpath.join(oncall_prober_base_url, "api/v0"))
    oncall_prober_scrape_interval = env.int(
        "ONCALL_PROBER_SCRAPE_INTERVAL", 30)
    oncall_prober_log_level = env.log_level(
        "ONCALL_PROBER_LOG_LEVEL", logging.INFO)
    oncall_prober_metrics_port = env.int("ONCALL_PROBER_METRICS_PORT", 9081)
    oncall_prober_metrics_bind_address = env(
        "ONCALL_PROBER_METRICS_BIND_ADDRESS", "0.0.0.0")


PROBER_CREATE_USER_SCENARIO_TOTAL = Counter(
    "prober_create_user_scenario_total",
    "Total number of runs of create user scenario for oncall API by status",
    ["status"]
)
PROBER_CREATE_USER_SCENARIO_DURATION_MILLISECONDS = Gauge(
    "prober_create_user_scenario_duration_milliseconds",
    "Duration in miliseconds of create user scenario for oncall API"
)

PROBER_DELETE_USER_SCENARIO_TOTAL = Counter(
    "prober_delete_user_scenario_total",
    "Total number of runs of delete user scenario by status",
    ["status"]
)

PROBER_DELETE_USER_SCENARIO_DURATION_MILLISECONDS = Gauge(
    "prober_delete_user_scenario_duration_milliseconds",
    "Duration in milliseconds of delete user scenario for oncall API"
)

PROBER_CREATE_TEAM_SCENARIO_TOTAL = Counter(
    "prober_create_team_scenario_total",
    "Total number of runs of create team scenario for oncall api by status",
    ["status"]
)
PROBER_CREATE_TEAM_SCENARIO_DURATION_MILLISECONDS = Gauge(
    "prober_create_team_scenario_duration_milliseconds",
    "Duration in miliseconds of create team scenario for oncall API"
)

PROBER_DELETE_TEAM_SCENARIO_TOTAL = Counter(
    "prober_delete_team_scenario_total",
    "Total number of runs of delete team scenario by status",
    ["status"]
)
PROBER_DELETE_TEAM_SCENARIO_DURATION_MILLISECONDS = Gauge(
    "prober_delete_team_scenario_duration_milliseconds",
    "Duration in miliseconds of delete team scenario for oncall API"
)

PROBER_CREATE_EVENT_SCENARIO_TOTAL = Counter(
    "prober_create_event_scenario_total",
    "Total number of runs of create event scenario for oncall api by status",
    ["status"]
)
PROBER_CREATE_EVENT_SCENARIO_DURATION_MILLISECONDS = Gauge(
    "prober_create_event_scenario_duration_milliseconds",
    "Duration in miliseconds of create event scenario for oncall API"
)

PROBER_LOAD_FRONTPAGE_SCENARIO_TOTAL = Counter(
    "prober_load_frontpage_scenario_total",
    "Total number of runs of load frontpage scenario for oncall by status",
    ["status"]
)
PROBER_LOAD_FRONTPAGE_SCENARIO_DURATION_MILLISECONDS = Gauge(
    "prober_load_frontpage_scenario_duration_milliseconds",
    "Duration in miliseconds of load frontpage scenario for oncall"
)


class OncallApi:
    def __init__(self, config: Config) -> None:
        self.base_url = config.oncall_prober_base_url
        self.api_url = config.oncall_prober_api_url

    def create_user(self, username: str) -> requests.Response:
        return requests.post(f"{self.api_url}/users", json={
            "name": username
        })

    def delete_user(self, username: str) -> requests.Response:
        return requests.delete(f"{self.api_url}/users/{username}")

    def create_team(self, username: str, password: str, team_name: str) -> requests.Response:
        sess = requests.Session()
        resp = sess.post(f"{self.base_url}/login", data={
            "username": username,
            "password": password
        })

        csrf = resp.json()["csrf_token"]

        return sess.post(f"{self.api_url}/teams", json={
            "name": team_name,
            "scheduling_timezone": "EU/Moscow",
        }, headers={
            "X-CSRF-TOKEN": csrf
        })

    def delete_team(self, team_name: str) -> requests.Response:
        return requests.delete(f"{self.api_url}/teams/{team_name}")

    def create_event(self,
                     team_name: str,
                     user_name: str,
                     role: str,
                     start: datetime.datetime,
                     duration: datetime.timedelta) -> requests.Response:
        return requests.post(f"{self.api_url}/events", json={
            "start": int(start.timestamp()),
            "end": int((start+duration).timestamp()),
            "user": user_name,
            "role": role,
            "team": team_name
        })


class ProbeScenario:

    def __init__(self, status_counter: Counter, test_time_ms: Gauge) -> None:
        self.status_counter = status_counter
        self.test_time_ms = test_time_ms

    def run(self) -> bool:
        self.status_counter.labels("any").inc()
        start = time.perf_counter()
        try:
            probe_success = self.on_test()
        except Exception as err:
            logging.error(err)
            probe_success = False
        end = time.perf_counter()

        duration = end-start

        if probe_success:
            self.status_counter.labels("success").inc()
        else:
            self.status_counter.labels("fail").inc()

        self.test_time_ms.set(duration * 1000)
        return probe_success

    def on_test(self) -> bool:
        raise NotImplementedError


class UserCreationProbe(ProbeScenario):
    def __init__(self,
                 status_counter: Counter,
                 test_time_ms: Gauge,
                 api: OncallApi,
                 test_user: str) -> None:
        super().__init__(status_counter, test_time_ms)
        self.api = api
        self.test_user = test_user

    def on_test(self) -> bool:
        response = self.api.create_user(self.test_user)
        return response.status_code == 201


class UserDeletionProbe(ProbeScenario):
    def __init__(self,
                 status_counter: Counter,
                 test_time_ms: Gauge,
                 api: OncallApi,
                 test_user: str) -> None:
        super().__init__(status_counter, test_time_ms)
        self.api = api
        self.test_user = test_user

    def on_test(self) -> bool:
        response = self.api.delete_user(self.test_user)
        return response.status_code == 200


class TeamCreationProbe(ProbeScenario):
    def __init__(self,
                 status_counter: Counter,
                 test_time_ms: Gauge,
                 api: OncallApi,
                 test_user: str,
                 test_team: str) -> None:
        super().__init__(status_counter, test_time_ms)
        self.api = api
        self.test_user = test_user
        self.test_team = test_team

    def on_test(self) -> bool:
        response = self.api.create_team(
            self.test_user, self.test_user, self.test_team)
        return response.status_code == 201


class TeamDeletionProbe(ProbeScenario):
    def __init__(self,
                 status_counter: Counter,
                 test_time_ms: Gauge,
                 api: OncallApi,
                 test_team: str) -> None:
        super().__init__(status_counter, test_time_ms)
        self.api = api
        self.test_team = test_team

    def on_test(self) -> bool:
        response = self.api.delete_team(self.test_team)
        return response.status_code == 200


def next_weekday(d, weekday):
    days_ahead = (weekday - d.weekday()) % 7
    return d + datetime.timedelta(days_ahead)


class EventCreationProbe(ProbeScenario):
    def __init__(self,
                 status_counter: Counter,
                 test_time_ms: Gauge,
                 api: OncallApi,
                 test_user: str,
                 test_team: str) -> None:
        super().__init__(status_counter, test_time_ms)
        self.api = api
        self.test_team = test_team
        self.test_user = test_user

    def on_test(self) -> bool:
        next_monday = next_weekday(datetime.datetime.now(), 0)
        duration = datetime.timedelta(days=2)
        response = self.api.create_event(
            self.test_team, self.test_user, "primary", next_monday, duration
        )
        return response.status_code == 201


class FrontpageLoadProbe(ProbeScenario):
    def __init__(self, status_counter: Counter, test_time_ms: Gauge, url: str) -> None:
        super().__init__(status_counter, test_time_ms)
        self.url = url

        self.options = ChromiumOptions()

        self.options.add_argument("--headless")
        self.options.add_argument("--disable-application-cache")
        self.driver = None

    def on_test(self) -> bool:
        self.driver.get(self.url)
        result = "Oncall" in self.driver.page_source
        self.driver.get("about:blank")
        return result

    def run(self) -> bool:
        try:
            self.driver = webdriver.Chrome(options=self.options)
            self.driver.get("about:blank")
            return super().run()
        finally:
            self.driver.quit()


def init_probes(api: OncallApi, config: Config) -> List[ProbeScenario]:
    test_user = "test_prober_user"
    test_team = "test_prober_team"

    return [UserCreationProbe(
        PROBER_CREATE_USER_SCENARIO_TOTAL,
        PROBER_CREATE_USER_SCENARIO_DURATION_MILLISECONDS,
        api, test_user
    ),
        TeamCreationProbe(
        PROBER_CREATE_TEAM_SCENARIO_TOTAL,
        PROBER_CREATE_TEAM_SCENARIO_DURATION_MILLISECONDS,
        api, test_user, test_team
    ),
        EventCreationProbe(
        PROBER_CREATE_EVENT_SCENARIO_TOTAL,
        PROBER_CREATE_EVENT_SCENARIO_DURATION_MILLISECONDS,
        api, test_user, test_team
    ),

        TeamDeletionProbe(
        PROBER_DELETE_TEAM_SCENARIO_TOTAL,
        PROBER_DELETE_TEAM_SCENARIO_DURATION_MILLISECONDS,
        api, test_team
    ),
        UserDeletionProbe(
        PROBER_DELETE_USER_SCENARIO_TOTAL,
        PROBER_DELETE_USER_SCENARIO_DURATION_MILLISECONDS,
        api, test_user
    ),

        FrontpageLoadProbe(
        PROBER_LOAD_FRONTPAGE_SCENARIO_TOTAL,
        PROBER_LOAD_FRONTPAGE_SCENARIO_DURATION_MILLISECONDS,
        config.oncall_prober_base_url
    )
    ]


def setup_logging(config: Config):
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%H:%M:%S',
        level=config.oncall_prober_log_level)


def main():
    config = Config()
    setup_logging(config)

    logging.info(
        f"starting up prober exporter on http://{config.oncall_prober_metrics_bind_address}:{config.oncall_prober_metrics_port}")
    start_http_server(addr=config.oncall_prober_metrics_bind_address,
                      port=config.oncall_prober_metrics_port)

    api = OncallApi(config)

    probes = init_probes(api, config)

    while True:
        logging.debug("running prober")

        for probe in probes:
            probe.run()

        logging.debug(
            f"waiting {config.oncall_prober_scrape_interval} seconds for next iteration")
        time.sleep(config.oncall_prober_scrape_interval)


def terminate(signal, frame):
    print("Terminating")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    main()
