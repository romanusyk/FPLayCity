import json
import csv
import os
import argparse
import glob
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


def find_latest_season_files(season: str = "2025-2026") -> tuple[str, str]:
    """Find the latest fixtures and bootstrap files for a season."""
    # Get the script directory and find data directory
    script_dir = Path(__file__).parent
    data_dir = script_dir.parent.parent.parent / "data" / season
    
    fixtures_dir = data_dir / "fixtures"
    bootstrap_dir = data_dir / "bootstrap"
    
    if not fixtures_dir.exists():
        raise FileNotFoundError(f"Fixtures directory not found: {fixtures_dir}")
    if not bootstrap_dir.exists():
        raise FileNotFoundError(f"Bootstrap directory not found: {bootstrap_dir}")
    
    fixtures_path = find_latest_file(str(fixtures_dir))
    bootstrap_path = find_latest_file(str(bootstrap_dir))
    
    return fixtures_path, bootstrap_path


def load_bootstrap_data(bootstrap_path: str) -> Dict[int, str]:
    """Load team ID to short_name mapping from bootstrap data."""
    with open(bootstrap_path, 'r') as f:
        data = json.load(f)
    
    team_mapping = {}
    for team in data['teams']:
        team_mapping[team['id']] = team['short_name']
    
    return team_mapping


def dump_fdr(fixtures_path: str, bootstrap_path: str, first_gw: Optional[int] = None, last_gw: Optional[int] = None) -> List[Dict]:
    """
    Extract fixture difficulty rating data from fixtures JSON.
    
    Args:
        fixtures_path: Path to fixtures response body JSON
        bootstrap_path: Path to bootstrap response body JSON  
        first_gw: First gameweek to include (optional)
        last_gw: Last gameweek to include (optional)
        
    Returns:
        List of fixture dictionaries, one per (team, gameweek) combination
    """
    # Load team mappings
    team_mapping = load_bootstrap_data(bootstrap_path)
    
    # Load fixtures data
    with open(fixtures_path, 'r') as f:
        fixtures = json.load(f)
    
    fdr_data = []
    
    for fixture in fixtures:
        gameweek = fixture['event']
        
        # Filter by gameweek range if specified
        if first_gw is not None and gameweek < first_gw:
            continue
        if last_gw is not None and gameweek > last_gw:
            continue
            
        team_h_id = fixture['team_h']
        team_a_id = fixture['team_a']
        
        # Get team short names
        team_h_name = team_mapping.get(team_h_id, f"Team{team_h_id}")
        team_a_name = team_mapping.get(team_a_id, f"Team{team_a_id}")
        
        # Generate score string
        score = "0:0"  # default
        if fixture['team_h_score'] is not None and fixture['team_a_score'] is not None:
            score = f"{fixture['team_h_score']}:{fixture['team_a_score']}"
        
        # Home team record
        home_record = {
            'gameweek': gameweek,
            'team': team_h_name,
            'home_away': 'H',
            'difficulty': fixture['team_h_difficulty'],
            'opponent': team_a_name,
            'score': score
        }
        
        # Away team record  
        away_record = {
            'gameweek': gameweek,
            'team': team_a_name,
            'home_away': 'A', 
            'difficulty': fixture['team_a_difficulty'],
            'opponent': team_h_name,
            'score': score
        }
        
        fdr_data.extend([home_record, away_record])
    
    return fdr_data


def dump_fdr_csv(fixtures_path: str, bootstrap_path: str, first_gw: Optional[int] = None, last_gw: Optional[int] = None) -> None:
    """
    Wrapper to call dump_fdr() and save output as CSV.
    
    Args:
        fixtures_path: Path to fixtures response body JSON
        bootstrap_path: Path to bootstrap response body JSON
        first_gw: First gameweek to include (optional)
        last_gw: Last gameweek to include (optional)
    """
    # Get FDR data
    fdr_data = dump_fdr(fixtures_path, bootstrap_path, first_gw, last_gw)
    
    # Ensure dumps directory exists
    data_dir = Path(fixtures_path).parent.parent  # Go up from fixtures/ to data/
    dumps_dir = data_dir / 'dumps'
    dumps_dir.mkdir(exist_ok=True)
    
    # Write CSV
    csv_path = dumps_dir / 'fdr.csv'
    
    if fdr_data:
        fieldnames = ['gameweek', 'team', 'home_away', 'difficulty', 'opponent', 'score']
        
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(fdr_data)
        
        print(f"FDR data written to {csv_path}")
        print(f"Total records: {len(fdr_data)}")
    else:
        print("No FDR data found for the specified criteria")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='Dump Fantasy Premier League Fixture Difficulty Ratings')
    parser.add_argument('fixtures_path', nargs='?', help='Path to fixtures response body JSON file (optional, auto-detects latest for 2025-2026)')
    parser.add_argument('bootstrap_path', nargs='?', help='Path to bootstrap response body JSON file (optional, auto-detects latest for 2025-2026)')
    parser.add_argument('--first-gw', type=int, help='First gameweek to include')
    parser.add_argument('--last-gw', type=int, help='Last gameweek to include')
    
    args = parser.parse_args()
    
    # Auto-detect files if not provided
    if args.fixtures_path is None or args.bootstrap_path is None:
        print("Auto-detecting latest files for 2025-2026 season...")
        auto_fixtures_path, auto_bootstrap_path = find_latest_season_files()
        
        fixtures_path = args.fixtures_path or auto_fixtures_path
        bootstrap_path = args.bootstrap_path or auto_bootstrap_path
        
        print(f"Using fixtures: {fixtures_path}")
        print(f"Using bootstrap: {bootstrap_path}")
    else:
        fixtures_path = args.fixtures_path
        bootstrap_path = args.bootstrap_path
    
    dump_fdr_csv(
        fixtures_path=fixtures_path,
        bootstrap_path=bootstrap_path, 
        first_gw=args.first_gw,
        last_gw=args.last_gw
    )


if __name__ == '__main__':
    main()
