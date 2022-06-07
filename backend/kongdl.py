import json
import os
import re
import time
import zlib
from base64 import b64encode
from pathlib import Path
from multiprocessing.pool import ThreadPool
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from backend.debugLib import trace
from backend.checkpoint import State

# TODO: make all I/O async?


def getUserSettings():
    with SETTINGS_PATH.open( "r") as settings:
        return json.load(settings)


SETTINGS_PATH = Path("settings.txt")
USER_SETTINGS = getUserSettings()
ENABLE_THUMBS = USER_SETTINGS["alsoDownloadThumbnails"]
ZLIB_COMPRESS = USER_SETTINGS["zlibCompression"]
ARCHIVE_DIR = Path("Archived Levels")
POOL = ThreadPool(10)
# How often we save - how many iterations between a save.
NUM_TO_SAVE=10


# Displays percentage based on goal and current value
def percentDone(current, goal):
    return "%.2f%% done" % ((float(goal) / float(current)) * 100)


# Gets the last two components from the path of a url.
def cleanGameUrl(url):
    path = urlparse(url).path.split('/')
    return {"author": path[-2], "game": path[-1]}


# Returns the text inside brackets
def getInsideBrackets(text):
    # This should be bullet proof against script kiddie levelnames
    return text[text.index("{") : text.rindex("}); return false") + 1]


# Return list of r.content using 10 threads pool
def getThumbs(urls):
    imapThumbs = POOL.imap(getThumb, urls)
    return [thumb for thumb in imapThumbs]


# Return r.content for given url
def getThumb(url, wait_sec: float = 0.1, max_tries=10):
    # Quick syntax for a for-loop. Value is discarded, because we don't need it.
    for __ in range(max_tries):
        try:
            r = requests.get(url)
            if r.status_code == 200:
                return b64encode(r.content).decode("ascii")
            trace("warn", "getThumb status_code: %s, retrying..." % r.status_code)
        except requests.ConnectionError:
            trace("warn", "getThumb ConnectionError, retrying...")
        time.sleep(wait_sec)


# View all dictionary items for levels
def debugLevels(levels):
    for level in levels:
        for k, v in level.items():
            print("%-12s : %s" % (k, v))
        print()


# Fetch content types for given author+game
def getContentTypes(author, game):
    """
    r = retryRequest("https://www.kongregate.com/games/%s/%s"%(author, game))
    soup = BeautifulSoup(r.text, "html5lib")
    # Objective: //*[@id="game_shared_contents"]/p/a
    #            #game_shared_contents > p > a
    """
    # TODO - room for improvement here.
    # TODO: check that this shouldn't be a retryRequest?
    r = requests.get("https://www.kongregate.com/games/%s/%s" % (author, game))
    # TODO: check what this is doing.
    results = re.findall("holodeck.showSharedContentsIndex(.*)", r.text)
    results2 = (result.replace("&quot;", '"') for result in results)
    deduped = set()
    for res in results2:
        # The content type will be surrounded by quotes.
        temp = res[res.index('"')+1:]
        deduped.add(temp[:temp.index('"')])
    return list(deduped)


# Extract important data out of html
# TODO: generator comprehension syntax, please.
# TODO: all of these find_all() things should be turned to find_next().
def extractData(soup):
    # Subsoup contains thumbnails and leveldata
    subSoup = soup.find_all("dt", class_="thumbnail")
    # Levels is list version of json leveldata
    # &quot; replacement isn't constantly needed. But does appear sometimes.
    levels = [
        json.loads(getInsideBrackets(str(text).replace("&quot;", '"')))
        for text in subSoup
    ]
    # Meta contains descriptions and author names
    meta = [meta for meta in soup.find_all("dd", class_="name_description")]
    plays = [
        int(
            load.find("em")
            .text.replace("Loaded ", "")
            .replace(" times", "")
            .replace("time", "")
        )
        for load in soup.find_all("dd", class_="load_count")
    ]
    ratings = [
        rating for rating in soup.find_all("div", class_="shared_content_rating")
    ]

    if (
        len(levels) != len(meta)
        or len(levels) != len(plays)
        or len(levels) != len(ratings)
    ):
        print("Not all lens are the same!")
        print(len(levels))
        print(len(meta))
        print(len(plays))
        print(len(ratings))

    thumbs = []
    if ENABLE_THUMBS:
        # TODO: this search pattern looks *very* brittle.
        # Furthermore: couldn't the work of find()ing be done in-pool rather than in-generator?
        thumbUrls = (thumb.find("img")["src"].split("?")[0] for thumb in subSoup)
        thumbs = getThumbs(thumbUrls)

    return POOL.starmap(
        extractData_inner, zip(*padAll([levels, meta, plays, ratings, thumbs]))
    )


# Extracted from extractData(), just the object-formatting step.
# Will be starmap()ed on the thread pool.
# Cannot assume anything about the None-ness of its arguments.
def extractData_inner(level, meta, plays, rating, thumb):
    levelInfo = {}
    if level != None:
        levelInfo.update(
            {
                "name": level["name"],
                "data": level["content"],
                "id": level["id"],
                "type": level["contentType"],
            }
        )
    if plays != None:
        levelInfo["plays"] = plays
    if meta != None:
        levelInfo["author"] = meta.find("em").text[3:]
        # Check if description is empty, if yes then don't make entry.
        desc = meta.find("p").text
        if len(desc) != 0:
            levelInfo["desc"] = desc
    if rating != None:
        rating = rating.find("em")
    if rating != None:
        levelInfo["rating"] = float(rating.text.replace(" Avg.)", "").replace("(", ""))
    if ENABLE_THUMBS and thumb != None:
        levelInfo["thumb"] = thumb
    return levelInfo


# Takes an iterable of arrays, and pads all of them to the length of the longest one.
# Padding occurs with padWith, which is None by default.
def padAll(arrays, padWith=None):
    highlen = max((len(arr) for arr in arrays))
    return [arr + [padWith] * (highlen - len(arr)) for arr in arrays]


# Make sure every folder required exists
def folderCheck(author, game):
    targetDir = ARCHIVE_DIR / author / game
    if not targetDir.exists():
        os.makedirs(targetDir)


# Saves level entry
def saveData(author, game, data):
    safeQuit = False
    try:
        dataDir = (
            ARCHIVE_DIR / author / game / "{}.json".format(str(data["id"]))
        )
        with dataDir.open("wb") as writeData:
            if ZLIB_COMPRESS == True:
                writeData.write(zlib.compress(json.dumps(data)))
            else:
                writeData.write(json.dumps(data, indent=4).encode("ascii"))
    except KeyboardInterrupt:
        safeQuit = True
        pass
    if safeQuit:
        trace("info", "Safely exited from IO operation.")
        exit()


# Retry request forever until success
def retryRequest(url, params={}, wait_sec: float = 0.1, max_tries=10):
    for __ in range(max_tries):
        try:
            r = requests.get(url, params=params)
            if r.status_code == 200:
                return r
            trace("warn", "retryRequest status_code: %s, retrying..." % r.status_code)
        except requests.ConnectionError:
            trace("warn", "retryRequest ConnectionError, retrying...")
        time.sleep(wait_sec)

def doContentType(templateUrl, author, game, contentType):
    state = State.load(author, game, contentType)
    if state == None:
        # Template in the last field - the one that changes.
        nextUrl = templateUrl + "/shared/%s" % contentType
        r = retryRequest(nextUrl, params={"srid": "last"})
        soup = BeautifulSoup(r.text, "html5lib")
        levels = extractData(soup)
        # Obtain lowest id while at last page.
        finalId = min([int(level["id"]) for level in levels])
        state = State(author, game, contentType, finalId, nextUrl)
    # Not providing srid brings us to first page
    while True:
        state.toNextSave += 1
        if (state.toNextSave >= NUM_TO_SAVE):
            state.save()
        
        r = retryRequest(state.nextUrl)
        soup = BeautifulSoup(r.text, "html5lib")
        levels = extractData(soup)
        # For each level entry, save. We can do it in parallel.
        POOL.map(lambda level: saveData(state.author, state.game, level), levels)
        lowestId = min((int(level["id"]) for level in levels))
        if lowestId == state.finalId:
            trace("info", "Final id has been found. Enjoy your archive!")
            break
        # Get the url to the next page of assets
        try:
            nextSoup = soup.find("li", class_="next")
            next = nextSoup.find("a", href=True)["href"]
            state.nextUrl = "http://www.kongregate.com" + next
        except:
            print("Error at {}, continuing.".format(state.nextUrl))
            break

        trace(
            "info",
            "Downloading %s/%s/%s: " % (state.author, state.game, state.contentType)
            + percentDone(lowestId, state.finalId),
        )
    state.save()

# Fetches all currently active asset id's
def main(author, game):
    contentTypes = getContentTypes(author, game)
    trace("info", "Found %s content types: %s" % (len(contentTypes), contentTypes))
    folderCheck(author, game)
    # Pre-template the first two, they don't change.
    templateUrl = "http://www.kongregate.com/games/%s/%s" % (author, game)
    POOL.starmap(doContentType, [(templateUrl, author, game, contentType) for contentType in contentTypes])
