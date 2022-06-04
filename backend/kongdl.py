import requests
from requests import ConnectionError
import os
import json
import zlib
import time
from bs4 import BeautifulSoup
from base64 import b64encode
from multiprocessing.pool import ThreadPool
import re

# TODO: make all I/O async?

from backend.debugLib import trace

def getUserSettings():
    with open(SETTINGS_PATH, "r") as settings:
        return json.loads(settings.read())

SETTINGS_PATH = "settings.txt"
USER_SETTINGS = getUserSettings()
ENABLE_THUMBS = USER_SETTINGS["alsoDownloadThumbnails"]
ZLIB_COMPRESS = USER_SETTINGS["zlibCompression"]
ARCHIVE_DIR = "Archived Levels"
# TODO: WTF is this for? Just thumbnails? Nope, I'm using this for other paralellizable tasks.
POOL = ThreadPool(10)


# Displays percentage based on goal and current value
def percentDone(current, goal):
    return "%.2f%% done"%((float(goal)/float(current))*100)

# Sanitizes the game url
def cleanGameUrl(url):
    url = url.split("/")
    return {"author":url[4],
            "game":url[5]}

# Returns the text inside brackets
def getInsideBrackets(text):
    # This should be bullet proof against script kiddie levelnames
    return text[ text.index("{") : text.rindex("}); return false") + 1 ]

# Construct base html soup
def makeSoup(html):
    return BeautifulSoup(html, "html.parser")

# Return list of r.content using 10 threads pool
def getThumbs(urls):
    # TODO: using imap instead of map is pointless if urls isn't a generator.
    imapThumbs = POOL.imap(getThumb, urls)
    return [thumb for thumb in imapThumbs]

# Return r.content for given url
# TODO: timeout to avoid being blocked.
# TODO: soft-fail at something lower than max recursion depth?
# TODO: make this a loop instead of recursive nonsense.
def getThumb(url):
    try:
        r = requests.get(url)
        if r.status_code == 200:
            return r.content
        trace("warn", "getThumb status_code: %s, retrying..."%r.status_code)
    except requests.ConnectionError:
        trace("warn", "getThumb ConnectionError, retrying...")
    return getThumb(url)

# View all dictionary items for levels
def debugLevels(levels):
    for level in levels:
        for k,v in level.items():
            print("%-12s : %s"%(k,v))
        print()

# Fetch content types for given author+game
def getContentTypes(author, game):
    '''
    r = retryRequest("https://www.kongregate.com/games/%s/%s"%(author, game))
    soup = makeSoup(r.text)
    # Objective: //*[@id="game_shared_contents"]/p/a
    #            #game_shared_contents > p > a
    '''
    # TODO - room for improvement here.
    # TODO: check that this shouldn't be a retryRequest?
    r = requests.get("https://www.kongregate.com/games/%s/%s"%(author, game))
    # TODO: check what this is doing
    results = re.findall("holodeck.showSharedContentsIndex(.*)", r.text)
    # TODO: generator comprehension syntax?
    results = [result.replace("&quot;",'"') for result in results]
    # TODO: Seems incredibly fragile and brittle.
    results = [res[res.index('"')+1:res.index('"')+res.index(")")-2] for res in results]
    # TODO: aaah inefficient! This is O(n^2), I can do it in O(n) just by sorting. 
    # Or, even better, just use a set from beginning.
    dupecheck = []
    for result in results:
        if result not in dupecheck:
            dupecheck.append(result)
    # TODO: Can we return a generator here?
    return dupecheck

# Extract important data out of html
# TODO: generator comprehension syntax, please.
# TODO: all of these find_all() things should be turned to find_next().
def extractData(soup):
    # Subsoup contains thumbnails and leveldata
    subSoup = soup.find_all("dt", class_="thumbnail")
    # Levels is list version of json leveldata
    # &quot; replacement isn't constantly needed. But does appear sometimes.
    levels = [json.loads(
        getInsideBrackets(str(text).replace("&quot;",'"')))
        for text in subSoup]
    # Meta contains descriptions and author names
    meta = [meta for meta in soup.find_all("dd", class_="name_description")]
    plays = [int(load.find("em").text.replace("Loaded ","").replace(" times","").replace("time",""))
             for load in soup.find_all("dd", class_="load_count")]
    ratings = [rating for rating in soup.find_all("div", class_="shared_content_rating")]

    if ENABLE_THUMBS:
        # TODO: this should be a generator. Also, this search pattern looks *very* brittle.
        # Furthermore: couldn't the work of find()ing be done in-pool rather than in-generator?
        thumbUrls = [thumb.find("img")["src"].split("?")[0] for thumb in subSoup]
        thumbs = getThumbs(thumbUrls)

    extractedData = []
    # TODO: can this be done in a thread pool or does it have to be sequential?
    for x in range(len(levels)):
        level = {"name":  levels[x]["name"],
                 "data":  levels[x]["content"],
                 "id":    levels[x]["id"],
                 "type":  levels[x]["contentType"],
                 "author":meta[x].find("em").text[3:],
                 "plays" :plays[x],
                 }
        # Check if description is empty, if yes then don't make entry.
        desc = meta[x].find("p").text
        if len(desc) != 0:
            level["desc"] = desc
        rating = ratings[x].find("em")
        if rating != None:
            level["rating"] = float(rating.text.replace(" Avg.)","").replace("(",""))
        if ENABLE_THUMBS:
            # TODO: ugh make this be done in-pool, not here!
            level["thumb"] = b64encode(thumbs[x]).decode('ascii')
        extractedData.append(level)

    return extractedData

# Make sure every folder required exists
def folderCheck(author, game):
    if not os.path.exists(ARCHIVE_DIR):
        os.makedirs(ARCHIVE_DIR)
    authorDir = ARCHIVE_DIR + "/" + author
    gameDir = authorDir + "/" + game
    if author not in os.listdir(ARCHIVE_DIR):
        os.mkdir(authorDir)
    if game not in os.listdir(authorDir):
        os.mkdir(gameDir)

# Saves level entry
def saveData(author, game, data):
    safeQuit = False
    try:
        dataDir = ARCHIVE_DIR+"/"+author+"/"+game+"/"+str(data["id"])+".json"
        with open(dataDir, "wb") as writeData:
            if ZLIB_COMPRESS == True:
                writeData.write(zlib.compress(json.dumps(data)))
            else:
                writeData.write(json.dumps(data, indent=4).encode('ascii'))
    except KeyboardInterrupt:
        safeQuit = True
        pass
    if safeQuit:
        trace("info", "Safely exited from IO operation.")
        exit()

# Retry request forever until success
# TODO: timeout so that we don't get filtered by an IDS.
# TODO: soft-fail instead of looping forever?
def retryRequest(url, params={}):
    while True:
        try:
            r = requests.get(url, params=params)
            if r.status_code == 200:
                return r
            trace("warn", "retryRequest status_code: %s, retrying..."%r.status_code)
        except ConnectionError:
            trace("warn", "retryRequest ConnectionError, retrying...")

# Fetches all currently active asset id's
def main(author, game):
    # TODO: hard coded values?
    contentTypes = getContentTypes("player_03", "run-3")
    trace("info", "Found %s content types: %s"%(len(contentTypes), contentTypes))
    for contentType in contentTypes:
        # TODO: ummm, why are we doing this every time? The inputs don't change.
        folderCheck(author, game)
        # TODO: template the first two fields of this beforehand, so that we only repeat one replace.
        fetchUrl = "http://www.kongregate.com/games/%s/%s/shared/%s"%(author, game, contentType)
        r = retryRequest(fetchUrl, params={"srid":"last"})
        soup = makeSoup(r.text)
        levels = extractData(soup)
        # Obtain lowest id while at last page.
        finalId = min([int(level["id"]) for level in levels])
        # Not providing srid brings us to first page
        # TODO: point of this? why do we have fetchUrl at all?
        nextUrl = fetchUrl
        while True:
            r = retryRequest(nextUrl)
            soup = makeSoup(r.text)
            levels = extractData(soup)
            # For each level entry, save. TODO: this is confusing syntax. Fix it.
            # Also TODO: paralellize this.
            [saveData(author, game, level) for level in levels]
            # TODO: generator.
            lowestId = min([int(level["id"]) for level in levels])
            if lowestId == finalId:
                trace("info", "Final id has been found. Enjoy your archive!")
                break
            # Get the url to the next page of assets
            nextSoup = soup.find("li", class_="next")
            next = nextSoup.find("a", href=True)["href"]
            nextUrl = "http://www.kongregate.com" + next

            trace("info", "Downloading %s/%s/%s: "%(author,game,contentType)+percentDone(lowestId, finalId))
