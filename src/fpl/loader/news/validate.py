import argparse
import logging
import os
from typing import List

from src.fpl.loader.store.json import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.news.pl import SEASON
from src.fpl.models.immutable import NewsFact

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_bootstrap_players(season: str = SEASON) -> dict[int, str]:
    """Load player ID map {id: web_name} from bootstrap."""
    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/bootstrap"))
    snapshot = store.load_latest()
    if not snapshot or "elements" not in snapshot:
        raise ValueError("Bootstrap snapshot missing elements.")
    
    return {p["id"]: p["web_name"] for p in snapshot["elements"]}

def process_gemini_article(
    season: str,
    gameweek: int,
    collection: str,
    article_id: str,
    player_map: dict[int, str]
):
    """
    Read gemini output, validate, and write to facts layer.
    Path: data/{season}/news/{gw}/{collection}/gemini/{article_id}
    """
    # 1. Read Gemini output
    gemini_base_path = f"data/{season}/news/{gameweek}/{collection}/gemini/{article_id}"
    gemini_store = JsonSnapshotStore(SnapshotSpec(base_path=gemini_base_path))
    
    try:
        gemini_data = gemini_store.load_latest()
    except Exception:
        logger.warning(f"No Gemini data found for article {article_id} at {gemini_base_path}")
        return

    raw_facts = gemini_data.get("facts", [])
    if not raw_facts:
        logger.info(f"No facts found in Gemini output for article {article_id}")
        return

    validated_facts: List[NewsFact] = []
    
    # 2. Validate
    for item in raw_facts:
        pid = item.get("player_id")
        web_name = item.get("web_name")
        
        if pid not in player_map:
            raise ValueError(f"Player ID {pid} not found in system (Article {article_id})")
        
        system_name = player_map[pid]
        logger.debug(f"Validation: Player {pid} - System='{system_name}', LLM='{web_name}' (Article {article_id})")
        if web_name != system_name:
             raise ValueError(
                 f"Validation Mismatch for Player {pid}: System='{system_name}', LLM='{web_name}' "
                 f"(Article {article_id})"
             )
             
        # 3. Transform
        fact = NewsFact(
            player_id=pid,
            news_id=int(article_id),
            next_gameweek=gameweek,
            fact=item["fact"],
            form=float(item["form"]),
            availability=float(item["availability"])
        )
        validated_facts.append(fact)

    if not validated_facts:
        return

    # 4. Store to facts layer
    facts_base_path = f"data/{season}/news/{gameweek}/{collection}/facts/{article_id}"
    facts_store = JsonSnapshotStore(SnapshotSpec(base_path=facts_base_path))
    
    output_data = [
        {
            "player_id": f.player_id,
            "news_id": f.news_id,
            "next_gameweek": f.next_gameweek,
            "fact": f.fact,
            "form": f.form,
            "availability": f.availability
        }
        for f in validated_facts
    ]
    
    facts_store.write(output_data, delete_older=True)
    logger.info(f"Validated and saved {len(validated_facts)} facts for article {article_id}")

def list_gemini_articles(season: str, gameweek: int, collection: str) -> List[str]:
    """List article IDs present in the gemini layer for a gameweek."""
    gemini_dir = f"data/{season}/news/{gameweek}/{collection}/gemini"
    if not os.path.isdir(gemini_dir):
        return []
    
    # Discover all article stores in the directory
    stores = JsonSnapshotStore.discover_stores(gemini_dir)
    
    # Extract article IDs from stores that have snapshots
    seen_article_ids: set[str] = set()
    for store in stores:
        if store.find_latest() is not None:
            # Extract article ID from base_path (last component)
            article_id_str = os.path.basename(store.base_path)
            seen_article_ids.add(article_id_str)
    
    return sorted(seen_article_ids)

def list_saved_facts(season: str, gameweek: int, collection: str) -> List[NewsFact]:
    """Load all validated facts for a gameweek."""
    facts_dir = f"data/{season}/news/{gameweek}/{collection}/facts"
    if not os.path.isdir(facts_dir):
        return []
    
    all_facts = []
    # Discover all article stores in the directory
    stores = JsonSnapshotStore.discover_stores(facts_dir)
    
    for store in stores:
        try:
            data = store.load_latest()  # data is list of dicts
            for item in data:
                all_facts.append(NewsFact(**item))
        except Exception:
            continue
            
    return all_facts

def main():
    parser = argparse.ArgumentParser(description="Validate extracted news facts.")
    parser.add_argument("news_collection", help="News collection (e.g. fpl_scout)")
    parser.add_argument("--last-gw", type=int, required=True, help="Target Gameweek")
    parser.add_argument("--first-gw", type=int, help="Optional start Gameweek (defaults to last-gw)")
    parser.add_argument("--article-id", type=int, action="append", help="Filter by article ID")
    
    args = parser.parse_args()
    
    first_gw = args.first_gw if args.first_gw is not None else args.last_gw
    last_gw = args.last_gw
    
    logger.info("Loading player context...")
    player_map = load_bootstrap_players()
    
    total_processed = 0
    
    for gw in range(first_gw, last_gw + 1):
        logger.info(f"Processing GW {gw}...")
        
        if args.article_id:
            target_ids = [str(aid) for aid in args.article_id]
        else:
            target_ids = list_gemini_articles(SEASON, gw, args.news_collection)
            
        for article_id in target_ids:
            try:
                process_gemini_article(
                    season=SEASON,
                    gameweek=gw,
                    collection=args.news_collection,
                    article_id=article_id,
                    player_map=player_map
                )
                total_processed += 1
            except Exception as e:
                logger.error(f"Error processing article {article_id} in GW {gw}: {e}")
                raise e

    logger.info(f"Finished. Processed {total_processed} articles.")

if __name__ == "__main__":
    main()
