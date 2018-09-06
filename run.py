import asyncio
import os
import re
import time
from urllib.parse import urlparse

import aiohttp
import gitlab
from dateutil import parser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from japronto import Application

GBIR_RELEASE_BY_SERVICE = bool(int(os.getenv('GBIR_RELEASE_BY_SERVICE', 0)))
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
GBIR_GET_TIMEOUT = int(os.getenv('GBIR_GET_TIMEOUT', 60))

QUEUE_PROJECTS = []
WORK_QUEUE_GETTING_REGISTRY = []
QUEUE_REGISTRY = []
WORK_QUEUE_GETTING_TAGS = []
QUEUE_TAGS = []


async def get_json(url):
    global GBIR_TOKEN
    try:
        async with aiohttp.ClientSession(headers={"PRIVATE-TOKEN": GBIR_TOKEN}) as session:
            async with session.get(url, timeout=GBIR_GET_TIMEOUT) as response:
                return await response.json()
    except asyncio.TimeoutError:
        return []
    except Exception as e:
        print(e)
        return []


async def parallel_client_session(method, data):
    global GBIR_TOKEN
    tasks = []
    async with aiohttp.ClientSession(headers={"PRIVATE-TOKEN": GBIR_TOKEN}) as session:
        for i in data:
            task = asyncio.ensure_future(method(i, session))
            tasks.append(task)
        return await asyncio.gather(*tasks)


async def get_projects():
    global QUEUE_PROJECTS
    for i in gl.projects.list(all=True):
        path_with_namespace = i.path_with_namespace
        if path_with_namespace not in QUEUE_PROJECTS:
            QUEUE_PROJECTS.append(path_with_namespace)
    print(f"{len(QUEUE_PROJECTS)} projects")


async def get_registry():
    global QUEUE_PROJECTS
    global WORK_QUEUE_GETTING_REGISTRY
    global QUEUE_REGISTRY

    if not len(QUEUE_PROJECTS):
        return
    path_with_namespace = QUEUE_PROJECTS.pop(0)
    # skip if this `path_with_namespace` in work
    if path_with_namespace in WORK_QUEUE_GETTING_REGISTRY:
        return
    WORK_QUEUE_GETTING_REGISTRY.append(path_with_namespace)
    try:
        data = await get_json(f"{GBIR_URL}/{path_with_namespace}/container_registry.json")
        if data:
            print(f"found {len(data)} images in {path_with_namespace}")
        for image in data:
            if image['tags_path'] not in QUEUE_REGISTRY:
                QUEUE_REGISTRY.append(image['tags_path'])
    except Exception as e:
        print(e)
    WORK_QUEUE_GETTING_REGISTRY.remove(path_with_namespace)


async def find_all_tags(tags_path):
    tags = []
    page = 1
    while True:
        data = await get_json(f"{GBIR_URL}{tags_path}&page={page}")
        page += 1
        if not data:
            break
        for tag in data:
            if await filter_tag(tag):
                tags.append(tag)
    return tags


async def filter_tag(data):
    this_time = time.time()
    if GBIR_RELEASE_BY_SERVICE and data['name'].startswith('release'):
        service_with_tag = data['location'].split('/')[-1:]
        service, tag = service_with_tag[0].split(":")
        if tag.replace('release-', '').startswith(service):
            print(f"+ {service}, {tag}")
            return None

        print(f"------> {service}, {tag}")

        if data['created_at'] is None:
            return None
        dt = parser.parse(data['created_at'])
        if dt.timestamp() < this_time - GBIR_FREEZE_TIME:
            return data
    elif data['name'] not in ['develop', 'master', 'latest'] and not data['name'].startswith('release'):
        if data['created_at'] is None:
            return None
        dt = parser.parse(data['created_at'])
        if dt.timestamp() < this_time - GBIR_FREEZE_TIME:
            return data
    else:
        return None


async def get_tags():
    global QUEUE_REGISTRY
    global WORK_QUEUE_GETTING_TAGS
    global QUEUE_TAGS

    if not len(QUEUE_REGISTRY):
        return
    tags_path = QUEUE_REGISTRY.pop(0)
    # skip if this `tags_path` in work
    if tags_path in WORK_QUEUE_GETTING_TAGS:
        return
    WORK_QUEUE_GETTING_TAGS.append(tags_path)
    try:
        data = await find_all_tags(tags_path)
        if data:
            print(f"found {len(data)} tags in {tags_path} path")
        for tag in data:
            if tag not in QUEUE_TAGS:
                QUEUE_TAGS.append(tag)
    except Exception as e:
        print(e)
    WORK_QUEUE_GETTING_TAGS.remove(tags_path)


async def delete_tags():
    global QUEUE_TAGS

    if not len(QUEUE_TAGS):
        return
    remove_tag = QUEUE_TAGS.pop(0)
    regexp = f"{GBIR_REGISTRY_URL}/"
    project = re.sub(regexp, '', remove_tag['location'])
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{GBIR_CLEAN_URL}/extra_path?clean-token={GBIR_CLEAN_TOKEN}&path={project}",
                                   timeout=GBIR_DELETE_TIMEOUT) as response:
                text = await response.read()
                print(f"{project} - {text}")
    except Exception as e:
        print(f"Error {project} - {e}")


async def connect_scheduler():
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(get_projects, 'interval', seconds=int(os.getenv('GBIR_SECONDS_PROJECTS', 300)), max_instances=1)
    scheduler.add_job(get_registry, 'interval', seconds=int(os.getenv('GBIR_SECONDS_REGISTRY', 13)), max_instances=10)
    scheduler.add_job(get_tags, 'interval', seconds=int(os.getenv('GBIR_SECONDS_TAGS', 11)), max_instances=10)
    scheduler.add_job(delete_tags, 'interval', seconds=int(os.getenv('GBIR_SECONDS_DELETE_TAGS', 60)), max_instances=1)

    scheduler.start()


async def health_check(request):
    global QUEUE_PROJECTS
    global QUEUE_REGISTRY
    global QUEUE_TAGS
    return request.Response(
        json={
            "QUEUE_PROJECTS": QUEUE_PROJECTS,
            "QUEUE_REGISTRY": QUEUE_REGISTRY,
            "QUEUE_TAGS": QUEUE_TAGS
        },
        mime_type="application/json")


app = Application()
gl = gitlab.Gitlab(GBIR_URL, private_token=GBIR_TOKEN, api_version='4')
app.loop.run_until_complete(get_projects())
app.loop.run_until_complete(connect_scheduler())
router = app.router
router.add_route('/', health_check)
app.run(port=80)
