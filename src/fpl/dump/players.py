import json
import csv
import os
import argparse
import glob
import shutil
from pathlib import Path
from typing import List, Dict, Optional


def find_latest_file(directory: str, pattern: str = "response_body_*.json") -> str:
    """Find the latest timestamped file in a directory."""
    search_pattern = os.path.join(directory, pattern)
    files = glob.glob(search_pattern)
    
    if not files:
        raise FileNotFoundError(f"No files found matching pattern {search_pattern}")
    
    # Sort by filename (timestamps are in ISO format, so lexicographic sort works)
    latest_file = sorted(files)[-1]
    return latest_file


def find_latest_bootstrap_file(season: str = "2025-2026") -> str:
    """Find the latest bootstrap file for a season."""
    # Get the script directory and find data directory
    script_dir = Path(__file__).parent
    data_dir = script_dir.parent.parent.parent / "data" / season
    
    bootstrap_dir = data_dir / "bootstrap"
    
    if not bootstrap_dir.exists():
        raise FileNotFoundError(f"Bootstrap directory not found: {bootstrap_dir}")
    
    bootstrap_path = find_latest_file(str(bootstrap_dir))
    return bootstrap_path


def load_position_mapping(bootstrap_data: dict) -> Dict[int, str]:
    """Load element_type ID to position acronym mapping."""
    position_mapping = {}
    for element_type in bootstrap_data['element_types']:
        position_mapping[element_type['id']] = element_type['singular_name_short']
    return position_mapping


def load_team_mapping(bootstrap_data: dict) -> Dict[int, str]:
    """Load team ID to short_name mapping."""
    team_mapping = {}
    for team in bootstrap_data['teams']:
        team_mapping[team['id']] = team['short_name']
    return team_mapping


def get_numeric_fields(player: dict) -> Dict[str, any]:
    """Extract all numeric metrics from a player."""
    numeric_fields = {}
    
    for key, value in player.items():
        # Skip non-numeric fields, IDs/codes, and unwanted fields
        if key in ['id', 'code', 'team_code', 'element_type', 'team', 'squad_number', 
                   'photo', 'first_name', 'second_name', 'web_name', 'region', 
                   'team_join_date', 'birth_date', 'opta_code',
                   'corners_and_indirect_freekicks_text', 'direct_freekicks_text', 
                   'penalties_text', 'corners_and_indirect_freekicks_order',
                   'direct_freekicks_order', 'penalties_order', 'can_select', 
                   'can_transact', 'has_temporary_code']:
            continue
        
        # Include numeric values (int, float) and boolean flags
        if isinstance(value, (int, float, bool)) or value is None:
            numeric_fields[key] = value
            
    return numeric_fields


def dump_players(bootstrap_path: str) -> List[Dict]:
    """
    Extract player data from bootstrap JSON.
    
    Args:
        bootstrap_path: Path to bootstrap response body JSON
        
    Returns:
        List of player dictionaries with name, position, team, price and metrics
    """
    # Load bootstrap data
    with open(bootstrap_path, 'r') as f:
        bootstrap_data = json.load(f)
    
    # Load mappings
    position_mapping = load_position_mapping(bootstrap_data)
    team_mapping = load_team_mapping(bootstrap_data)
    
    players_data = []
    
    for player in bootstrap_data['elements']:
        # Basic player info
        player_record = {
            'name': f"{player['first_name']} {player['second_name']}",
            'position': position_mapping.get(player['element_type'], 'UNK'),
            'team': team_mapping.get(player['team'], f"Team{player['team']}"),
            'price': player['now_cost'] / 10.0,  # Convert from tenths to actual price
            # Injury/availability information
            'chance_of_playing_next_round': player.get('chance_of_playing_next_round'),
            'news': player.get('news', ''),
            'news_added': player.get('news_added'),
            'status': player.get('status', ''),
        }
        
        # Add all numeric metrics
        numeric_metrics = get_numeric_fields(player)
        player_record.update(numeric_metrics)
        
        players_data.append(player_record)
    
    return players_data


def dump_players_csv(bootstrap_path: str) -> None:
    """
    Wrapper to call dump_players() and save output as CSV.
    
    Args:
        bootstrap_path: Path to bootstrap response body JSON
    """
    # Get players data
    players_data = dump_players(bootstrap_path)
    
    # Ensure dumps directory exists
    data_dir = Path(bootstrap_path).parent.parent  # Go up from bootstrap/ to data/
    dumps_dir = data_dir / 'dumps'
    dumps_dir.mkdir(exist_ok=True)
    
    # Write CSV
    csv_path = dumps_dir / 'players.csv'
    
    if players_data:
        # Get all possible fieldnames from all players (in case some have different fields)
        all_fieldnames = set()
        for player in players_data:
            all_fieldnames.update(player.keys())
        
        # Order fieldnames with key fields first
        key_fields = ['name', 'position', 'team', 'price', 'chance_of_playing_next_round', 'news', 'news_added', 'status']
        other_fields = sorted([f for f in all_fieldnames if f not in key_fields])
        fieldnames = key_fields + other_fields
        
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(players_data)
        
        # Also save as TXT format
        txt_path = dumps_dir / 'players.txt'
        shutil.copy2(csv_path, txt_path)
        
        print(f"Players data written to {csv_path}")
        print(f"Players data also saved as {txt_path}")
        print(f"Total players: {len(players_data)}")
        print(f"Total columns: {len(fieldnames)}")
    else:
        print("No players data found")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='Dump Fantasy Premier League Players Data')
    parser.add_argument('bootstrap_path', nargs='?', help='Path to bootstrap response body JSON file (optional, auto-detects latest for 2025-2026)')
    
    args = parser.parse_args()
    
    # Auto-detect file if not provided
    if args.bootstrap_path is None:
        print("Auto-detecting latest bootstrap file for 2025-2026 season...")
        bootstrap_path = find_latest_bootstrap_file()
        print(f"Using bootstrap: {bootstrap_path}")
    else:
        bootstrap_path = args.bootstrap_path
    
    dump_players_csv(bootstrap_path=bootstrap_path)


if __name__ == '__main__':
    main()
