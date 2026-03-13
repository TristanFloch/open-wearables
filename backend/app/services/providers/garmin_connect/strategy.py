from app.services.providers.base_strategy import BaseProviderStrategy
from app.services.providers.garmin_connect.auth import GarminConnectAuth
from app.services.providers.garmin_connect.data_247 import GarminConnect247DataAdapter
from app.services.providers.garmin_connect.workouts import GarminConnectWorkouts


class GarminConnectStrategy(BaseProviderStrategy):
    """Garmin Connect provider via Garth (personal account, PULL-based).

    Uses Garth library for authentication (email/password + MFA)
    and data fetching. No webhooks or enterprise API required.
    """

    def __init__(self) -> None:
        super().__init__()
        self._auth = GarminConnectAuth(
            connection_repo=self.connection_repo,
        )
        self.workouts = GarminConnectWorkouts(auth=self._auth)
        self.data_247 = GarminConnect247DataAdapter(auth=self._auth)

    @property
    def name(self) -> str:
        return "garmin_connect"

    @property
    def api_base_url(self) -> str:
        return "https://connect.garmin.com"

    @property
    def display_name(self) -> str:
        return "Garmin Connect"

    @property
    def has_cloud_api(self) -> bool:
        # Returns False to prevent OAuth flow from being triggered.
        # Auth is handled via dedicated credential endpoints.
        return False
