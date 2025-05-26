import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from httpx import AsyncClient
from src.fpl.models.immutable import (
    Teams, Team, TeamFixture, Fixture, Fixtures, Players, Player,
    PlayerType, PlayerFixtures, PlayerFixture,
)

BASE_URL = "https://fantasy.premierleague.com/api/"
RESOURCES = {
    'main': {
        'url': 'bootstrap-static/',
        'dir_path': 'data/2024-2025/bootstrap',
    },
    'fixtures': {
        'url': 'fixtures/',
        'dir_path': 'data/2024-2025/fixtures',
    },
    'elements': {
        'url': 'element-summary/{resource_id}/',
        'dir_path': 'data/2024-2025/elements',
    },
}


def ensure_dir_exists(filepath: str) -> None:
    """Make sure the directory for the given filepath exists."""
    directory = os.path.dirname(filepath)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


class Season:

    s2425 = '2024-2025'


class BaseResource:

    def __init__(
            self,
            dir_path_template: str,
            resource_id: str | None = None,
    ):
        self.dir_path_template = dir_path_template
        self.resource_id = resource_id

    @property
    def dir_path(self) -> str:
        if self.resource_id:
            return self.dir_path_template.format(resource_id=self.resource_id)
        else:
            return self.dir_path_template

    def build_filename(self, current_dt: datetime) -> str:
        return os.path.join(
            self.dir_path,
            f'response_body_{current_dt.isoformat(timespec="seconds")}.json',
        )

    def get_all_states(self) -> list[datetime]:
        result = []
        for file_name in os.listdir(self.dir_path):
            if not file_name.endswith('.json'):
                continue
            prefix, dt_str = file_name.replace('.json', '').rsplit('_', 1)
            dt = datetime.fromisoformat(dt_str)
            result.append(dt)
        result.sort()
        return result

    @staticmethod
    def is_up_to_date(latest_state: datetime, freshness: int) -> bool:
        return datetime.now() - latest_state < timedelta(days=freshness)

    async def load(self, client: AsyncClient, current_dt: datetime, sleep_sec: float = 0.5) -> dict:
        raise NotImplemented

    async def get_latest_state(
            self,
            freshness: int,
            client: AsyncClient,
            sleep_sec: float = 0.5,
    ) -> dict:
        all_states = self.get_all_states()
        if all_states and self.is_up_to_date(all_states[-1], freshness):
            latest_state = all_states[-1]
        else:
            latest_state = datetime.now()
            await self.load(client, latest_state, sleep_sec=sleep_sec)
        with open(self.build_filename(latest_state), "r") as f:
            return json.load(f)


class SimpleResource(BaseResource):

    def __init__(
            self,
            url_template: str,
            dir_path_template: str,
            resource_id: str | None = None,
    ):
        super().__init__(dir_path_template, resource_id)
        self.url_template = url_template

    @property
    def url(self) -> str:
        if self.resource_id:
            return self.url_template.format(resource_id=self.resource_id)
        else:
            return self.url_template

    async def load(self, client: AsyncClient, current_dt: datetime, sleep_sec: float = 0.5) -> dict:
        logging.info('Calling %s', self.url)
        response = await client.get(url=BASE_URL + self.url)
        response.raise_for_status()
        response_body = json.loads(response.content)
        filepath = self.build_filename(current_dt)
        ensure_dir_exists(filepath)
        with open(filepath, "w") as f:
            json.dump(response_body, f, indent=4)
        await asyncio.sleep(sleep_sec)
        return response_body


class CompoundResource(BaseResource):

    resources: dict[str, SimpleResource]

    def __init__(self, parent_dir_path: str, child_url_template, resource_ids: list[str]):
        super().__init__(parent_dir_path)
        self.resources = {}
        for resource_id in resource_ids:
            self.resources[resource_id] = SimpleResource(
                url_template=child_url_template,
                dir_path_template=os.path.join(parent_dir_path, '{resource_id}/'),
                resource_id=resource_id,
            )

    async def load(self, client: AsyncClient, current_dt: datetime, sleep_sec: float = 0.5) -> dict:
        response_body = {}
        for resource_id, resource in self.resources.items():
            resource_body = await resource.load(client, current_dt, sleep_sec=sleep_sec)
            response_body[resource_id] = resource_body
        filepath = self.build_filename(current_dt)
        ensure_dir_exists(filepath)
        with open(filepath, "w") as f:
            json.dump(response_body, f, indent=4)
        return response_body


async def load(client: AsyncClient, freshness: int = 1):
    season = Season.s2425

    main_resource = SimpleResource('bootstrap-static/', f'data/{season}/bootstrap')
    fixtures_resource = SimpleResource('fixtures/', f'data/{season}/fixtures')

    main_response_body = await main_resource.get_latest_state(freshness, client)
    await fixtures_resource.get_latest_state(freshness, client)

    elements_resource = CompoundResource(
        parent_dir_path=f'data/{season}/elements',
        child_url_template='element-summary/{resource_id}/',
        resource_ids=[str(element['id']) for element in main_response_body['elements']],
    )
    await elements_resource.get_latest_state(freshness, client)


async def bootstrap(client: AsyncClient):
    season = Season.s2425
    freshness = 1000

    main_resource = SimpleResource('bootstrap-static/', f'data/{season}/bootstrap')
    fixtures_resource = SimpleResource('fixtures/', f'data/{season}/fixtures')

    main_response_body = await main_resource.get_latest_state(freshness, client)
    fixtures_response_body = await fixtures_resource.get_latest_state(freshness, client)

    elements_resource = CompoundResource(
        parent_dir_path=f'data/{season}/elements',
        child_url_template='element-summary/{resource_id}/',
        resource_ids=[str(element['id']) for element in main_response_body['elements']],
    )
    player_response_bodies = await elements_resource.get_latest_state(freshness, client)

    for row in main_response_body['teams']:
        Teams.add(
            Team(
                team_id=row['id'],
                name=row['name'],
                strength_overall_home=row['strength_overall_home'],
                strength_overall_away=row['strength_overall_away'],
                strength_attack_home=row['strength_attack_home'],
                strength_attack_away=row['strength_attack_away'],
                strength_defence_home=row['strength_defence_home'],
                strength_defence_away=row['strength_defence_away'],
            )
        )

    for row in fixtures_response_body:
        home = TeamFixture(
            fixture_id=row['id'],
            team_id=row['team_h'],
            difficulty=row['team_h_difficulty'],
            score=row['team_h_score'],
        )
        away = TeamFixture(
            fixture_id=row['id'],
            team_id=row['team_a'],
            difficulty=row['team_a_difficulty'],
            score=row['team_a_score'],
        )
        fixture = Fixture(
            fixture_id=row['id'],
            finished=row['finished'],
            gameweek=row['event'],
            home=home,
            away=away,
        )
        Fixtures.add(fixture)

    for player in main_response_body['elements']:
        Players.add(
            Player(
                player_id=player['id'],
                web_name=player['web_name'],
                player_type=PlayerType(player['element_type']),
                team_id=player['team'],
            )
        )

    for player_id, row in player_response_bodies.items():
        for fixture in row['history']:
            PlayerFixtures.add(
                PlayerFixture(
                    player_id=fixture['element'],
                    fixture_id=fixture['fixture'],
                    gameweek=fixture['round'],
                    was_home=fixture['was_home'],
                    total_points=fixture['total_points'],
                    minutes=fixture['minutes'],
                    goals_scored=fixture['goals_scored'],
                    assists=fixture['assists'],
                    expected_goals=float(fixture['expected_goals']),
                    expected_assists=float(fixture['expected_assists']),
                    expected_goal_involvements=float(fixture['expected_goal_involvements']),
                    expected_goals_conceded=float(fixture['expected_goals_conceded']),
                    value=fixture['value'],
                )
            )
        for fixture in row['fixtures']:
            PlayerFixtures.add(
                PlayerFixture(
                    player_id=int(player_id),
                    fixture_id=fixture['id'],
                    gameweek=fixture['event'],
                    was_home=fixture['is_home'],
                )
            )
