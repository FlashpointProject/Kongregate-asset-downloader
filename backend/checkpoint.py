import json
import os
from pathlib import Path

CHECKPOINT_DIR="Checkpoints"

class State:
    def __init__(self, author, game, contentType, finalId, nextUrl):
        self.author = author
        self.game = game
        self.contentType = contentType
        self.finalId = finalId
        self.nextUrl = nextUrl
        self.savepath = Path(CHECKPOINT_DIR) / author / game / "{}.json".format(contentType)
        self.toNextSave = 0
    
    @classmethod
    def load(cls, author, game, contentType):
        savepath = Path(CHECKPOINT_DIR) / author / game / "{}.json".format(contentType)
        if not savepath.exists():
            return None
        
        with open(savepath, 'r') as savefile:
            outDict = json.load(savefile)
        return cls(author, game, contentType, outDict["finalId"], outDict["nextUrl"])
    
    def save(self):
        if not self.savepath.exists():
            os.makedirs(self.savepath.parent)
        with open(self.savepath, 'w') as savefile:
            json.dump({
                "finalId": self.finalId,
                "nextUrl": self.nextUrl
            }, savefile)
        self.toNextSave = 0
