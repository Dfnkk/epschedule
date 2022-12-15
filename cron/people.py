import argparse
import datetime
import json
import os
import time

import requests
from google.cloud import secretmanager, storage
from requests.models import HTTPError

ENDPOINT_URL = "https://four11.eastsideprep.org/epschedule/people"
SECRET_REQUEST = {"name": "projects/epschedule-v2/secrets/four11_key/versions/1"}


def gen_auth_header(api_key):
    return {"Authorization": "Bearer {}".format(api_key)}


# Return the year of the current graduating class
# I.E. the 2019-2020 school year would return 2020
def get_current_school_year():
    now = datetime.datetime.now()
    end_year = now.year
    if now.month >= 7 or (now.month >= 6 and now.day >= 10):
        # Old school year has ended, add one to year
        end_year += 1
    return end_year


def add_free_periods_to_schedule(course_list):
    for period in PARSEABLE_PERIODS:
        contains = False
        for clss in course_list:
            if clss["period"] == period:
                contains = True
                break

        if not contains:
            course_list.append(FREE_PERIOD_CLASS.copy())
            course_list[-1]["period"] = period
    # Modifies course list in place


def decode_trimester_classes(four11_response):
    trimester_classes = []
    trimester = four11_response["sections"]
    for clss in trimester:
        if clss["period"] in PARSEABLE_PERIODS:
            obj = {
                "period": clss["period"],
                "room": clss["location"],
                "name": clss["course"],
                "teacher_username": clss["teacher"],
                "department": clss["department"],
            }
            trimester_classes.append(obj)

    add_free_periods_to_schedule(trimester_classes)
    trimester_classes.sort(key=lambda x: x["period"])
    return trimester_classes


def download_schedule(session, api_key, username, year):
    person = {"classes": []}

    # For each trimester
    for term_id in range(1, 4):
        req = session.get(
            ENDPOINT_URL.format(username),
            headers=gen_auth_header(api_key),
            params={"term_id": str(term_id)},
        )
        if req.status_code == 500:
            raise NameError("Student {} not found in four11 database".format(username))
        briggs_person = json.loads(req.content)
        person["classes"].append(decode_trimester_classes(briggs_person))

    individual = briggs_person["individual"]
    person["sid"] = individual["id"]
    if individual.get("preferred_name"):
        person["preferred_name"] = individual["preferred_name"]
    person["firstname"] = individual["firstname"]
    person["lastname"] = individual["lastname"]
    person["gradyear"] = individual["gradyear"]
    # Recompute the username, don't just stuff the one we were passed
    person["username"] = individual["email"].split("@")[0]

    # Find advisor
    person["advisor"] = None
    for section in briggs_person["sections"]:
        if "advisory" in section["course"].lower():
            person["advisor"] = section["teacher"]

    # Convert grade to gradyear
    person["grade"] = None
    if person["gradyear"]:
        person["grade"] = 12 - (person["gradyear"] - year)

    return person


def download_schedule_with_retry(session, api_key, username, year):
    for i in range(3):
        try:
            return download_schedule(session, api_key, username, year)
        except (HTTPError, ValueError) as e:  # catches HTTP and JSON errors
            print(f"Error for {username}: {e}, retrying")
            if i != 2:
                time.sleep(1)
            else:
                raise e


def crawl_schedules(dry_run=False, verbose=False):
    print(f"Starting schedule crawl, dry_run={dry_run}")

    start = time.time()
    # Load access key
    secret_client = secretmanager.SecretManagerServiceClient()
    secret_response = secret_client.access_secret_version(request=SECRET_REQUEST)
    key = secret_response.payload.data.decode("UTF-8")

    session = requests.Session()
    x = requests.get(ENDPOINT_URL, headers=gen_auth_header(key))
    print(x.text)

    # print(f"Schedule crawl completed, {len(schedules)} downloaded, {errors} errors")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="If set, the results are not uploaded to production.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print debugging output."
    )
    args = parser.parse_args()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "../service_account.json"
    crawl_schedules(args.dry_run, args.verbose)
