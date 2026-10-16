from . import CuraAiAssistant


def getMetaData():
    return {}


def register(app):
    return {"extension": CuraAiAssistant.CuraAiAssistant()}
