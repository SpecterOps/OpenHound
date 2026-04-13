from enum import Enum


class Progress(str, Enum):
    tqdm = "tqdm"
    log = "log"
    alive_progress = "alive_progress"
