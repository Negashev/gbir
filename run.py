import asyncio
import copy
import logging
import os
import re
import time
from random import shuffle
from urllib.parse import urlparse

import aiohttp
import gitlab
from dateutil import parser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from japronto import Application

GBIR_TOKEN = os.getenv('GBIR_TOKEN', None)
if GBIR_TOKEN is None:
    print(f"Please set GBIR_TOKEN")

GBIR_CLEAN_TOKEN = os.getenv('GBIR_CLEAN_TOKEN', None)
if GBIR_CLEAN_TOKEN is None:
    print(f"Please set GBIR_CLEAN_TOKEN")

try:
    o = urlparse(os.getenv('GBIR_URL', None))
    GBIR_URL = f"{o.scheme}://{o.netloc}"
except Exception as e:
    print(f"Error parse GBIR_URL: {e}")
try:
    o = urlparse(os.getenv('GBIR_CLEAN_URL', None))
    GBIR_CLEAN_URL = f"{o.scheme}://{o.netloc}"
except Exception as e:
    print(f"Error parse GBIR_CLEAN_URL: {e}")

GBIR_FREEZE_TIME = int(os.getenv('GBIR_FREEZE_TIME', 604800))
GBIR_DELETE_SIZE = int(os.getenv('GBIR_DELETE_SIZE', 10))
GBIR_DELETE_TIMEOUT = int(os.getenv('GBIR_DELETE_TIMEOUT', 600))
GBIR_REGISTRY_URL = os.getenv('GBIR_REGISTRY_URL', '')

PROJECTS = []
REGISTRY = []
TAGS = []


async def get_json(url):
    global GBIR_TOKEN
    async with aiohttp.ClientSession(headers={"PRIVATE-TOKEN": GBIR_TOKEN}) as session:
        async with session.get(url) as response:
            return await response.json()


async def parallel_client_session(method, data):
    global GBIR_TOKEN
    tasks = []
    async with aiohttp.ClientSession(headers={"PRIVATE-TOKEN": GBIR_TOKEN}) as session:
        for i in data:
            task = asyncio.ensure_future(method(i, session))
            tasks.append(task)
        return await asyncio.gather(*tasks)


async def get_projects():
    global PROJECTS
    PROJECTS = [i.path_with_namespace for i in gl.projects.list(all=True)]
    print(f"found {len(PROJECTS)} projects")


async def get_registry_in_project(path_with_namespace, session):
    async with session.get(f"{GBIR_URL}/{path_with_namespace}/container_registry.json") as response:
        return await response.json()


async def get_registry():
    global PROJECTS
    global REGISTRY
    this_PROJECTS = copy.copy(PROJECTS)
    this_REGISTRY = []
    for i in await parallel_client_session(get_registry_in_project, this_PROJECTS):
        this_REGISTRY = this_REGISTRY + i
    REGISTRY = this_REGISTRY
    print(f"found {len(REGISTRY)} images in {len(PROJECTS)} projects")


async def find_all_tags(data):
    tags_path = data['tags_path']
    tags = []
    page = 1
    while True:
        data = await get_json(f"{GBIR_URL}{tags_path}&page={page}")
        page += 1
        if not data:
            break
        for tag in data:
            tags.append(tag)
    return tags


async def filter_tag(data):
    this_time = time.time()
    if data['name'] not in ['develop', 'master', 'latest'] and not data['name'].startswith('release'):
        if data['created_at'] is None:
            return None
        dt = parser.parse(data['created_at'])
        if dt.timestamp() < this_time - GBIR_FREEZE_TIME:
            return data


async def get_tags():
    global PROJECTS
    global REGISTRY
    global TAGS
    this_TAGS = []
    # find tags
    tasks = []
    for i in REGISTRY:
        task = asyncio.ensure_future(find_all_tags(i))
        tasks.append(task)
    data = await asyncio.gather(*tasks)
    for i in data:
        this_TAGS = this_TAGS + i
    # filter_tag
    tasks = []
    for j in this_TAGS:
        task = asyncio.ensure_future(filter_tag(j))
        tasks.append(task)
    TAGS = [i for i in await asyncio.gather(*tasks) if i is not None]
    print(f"found {len(this_TAGS)} tags, {len(TAGS)} for remove in {len(REGISTRY)} images in {len(PROJECTS)} projects")


async def delete_tags():
    global TAGS
    this_TAGS = TAGS
    shuffle(this_TAGS)
    for i in this_TAGS[0:GBIR_DELETE_SIZE]:
        regexp = f"{GBIR_REGISTRY_URL}/"
        project = re.sub(regexp, '', i['location'])
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{GBIR_CLEAN_URL}/extra_path?clean-token={GBIR_CLEAN_TOKEN}&path={project}",
                                       timeout=GBIR_DELETE_TIMEOUT) as response:
                    text = await response.read()
                    print(f"{project} - {text}")
                    TAGS.remove(i)
        except Exception as e:
            print(f"Error {project} - {e}")


async def connect_scheduler():
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(get_projects, 'interval', seconds=int(os.getenv('GBIR_SECONDS_PROJECTS', 1200)), max_instances=1)
    scheduler.add_job(get_registry, 'interval', seconds=int(os.getenv('GBIR_SECONDS_REGISTRY', 600)), max_instances=1)
    scheduler.add_job(get_tags, 'interval', seconds=int(os.getenv('GBIR_SECONDS_TAGS', 600)), max_instances=1)
    scheduler.add_job(delete_tags, 'interval', seconds=int(os.getenv('GBIR_SECONDS_DELETE_TAGS', 60)), max_instances=1)

    scheduler.start()


async def health_check(request):
    global TAGS
    return request.Response(json=TAGS, mime_type="application/json")


app = Application()
gl = gitlab.Gitlab(GBIR_URL, private_token=GBIR_TOKEN, api_version='4')
logging.getLogger('apscheduler.scheduler').propagate = False
logging.getLogger('apscheduler.scheduler').addHandler(logging.NullHandler())
app.loop.run_until_complete(get_projects())
app.loop.run_until_complete(get_registry())
app.loop.run_until_complete(get_tags())
app.loop.run_until_complete(connect_scheduler())
router = app.router
router.add_route('/', health_check)
app.run(port=80)
