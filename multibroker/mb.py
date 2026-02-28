from multibroker.clients.alor.AlorClient import AlorClient


class MultiBroker:
    @staticmethod
    def create_alor_client(refresh_token: str | None = None, is_demo: bool = False) -> AlorClient:
        return AlorClient(refresh_token, is_demo=is_demo)
