class MBException(Exception):
    pass


class WebsocketReconnectionException(MBException):
    pass


class WebsocketError(MBException):
    pass


class WebsocketClosed(MBException):
    pass
