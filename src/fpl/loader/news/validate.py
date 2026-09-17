"""Validation layer: check what the LLM extracted before it becomes a fact.

Reads the `extracted` layer written by `src/fpl/loader/news/llm.py`, verifies every player
identity against the bootstrap snapshot, and writes `facts` for the models to read.

This layer is separate from extraction on purpose. The extractor is a language model and its reply
is checked for *shape* there; whether the player it names is the player it means can only be
checked against real data, and is checked here - loudly. A hallucinated id must never reach the
facts layer wearing the same shape as a real one.

Rejected facts are dropped, counted and logged - never silently
------------------------------------------------------------------
A language model occasionally attaches the right fact to the wrong player id; on the first live run
it filed Murillo's fact under 473, which is Aina. That is precisely what this layer is for, and the
fact is rejected. What it is *not* worth doing is aborting the gameweek: one bad row in one article
used to raise, which left every other article unvalidated and the news layer empty. So a rejection
is counted and logged loudly, and the run fails only when the rejection *rate* is high enough to
mean something systemic - the wrong season's bootstrap, say - rather than an ordinary miss.
`MAX_REJECT_RATE` is that line.

Layer naming: the extracted layer was called `gemini` until the extractor became `claude -p`.
`EXTRACTED_LAYER` is the current name and `LEGACY_EXTRACTED_LAYERS` lists the old ones, which are
still read so last season's stored extractions remain usable. Nothing is ever written to a legacy
layer.
"""
import argparse
import logging
import os
import unicodedata
from typing import List

from src.fpl.loader.store.json import JsonSnapshotStore, SnapshotSpec
from src.fpl.loader.news.llm import EXTRACTED_LAYER
from src.fpl.loader.news.pl import SEASON
from src.fpl.models.immutable import NewsFact

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


NON_DECOMPOSING = str.maketrans({
    'ø': 'o', 'Ø': 'O', 'đ': 'd', 'Đ': 'D', 'ð': 'd', 'Ð': 'D',
    'ł': 'l', 'Ł': 'L', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE',
    'ß': 'ss', 'þ': 'th', 'Þ': 'TH', "'": '', '-': ' ',
})
"""Letters NFKD leaves alone. `ø` is one character, not `o` plus a stroke, so stripping combining
marks does not touch it - which is how 'Odegaard' first halted a whole validation run."""


def comparable_name(name: str) -> str:
    """A name reduced to what two spellings of the same player must share.

    Diacritics are dropped, non-decomposing letters transliterated, case and punctuation ignored.
    The point is to forgive *spelling*, not identity: an LLM writing 'Odegaard' for 'Ødegaard' is
    reporting the right player, while 'Haaland' for 'Gabriel' still fails. The player id remains
    the key; this comparison is the cross-check that the id and the name agree.
    """
    folded = unicodedata.normalize('NFKD', name.translate(NON_DECOMPOSING))
    return ''.join(ch for ch in folded if not unicodedata.combining(ch)).casefold().strip()


MAX_REJECT_RATE = 0.25
"""Rejected-fact fraction above which the run fails instead of continuing.

An isolated wrong id is the extractor being a language model. A quarter of them wrong means the
player map and the articles disagree systematically - the usual cause is a bootstrap snapshot from
the wrong season, and continuing would file a gameweek of confidently wrong facts.
"""

LEGACY_EXTRACTED_LAYERS = ('gemini',)
"""Retired extractor layers, still read so stored seasons stay usable. Never written."""


def _extracted_dir(season: str, gameweek: int, collection: str) -> str | None:
    """The directory holding this gameweek's extractions, preferring the current layer.

    Returns None when neither the current nor any legacy layer exists, which is the normal state
    for a gameweek whose articles have not been processed yet.
    """
    base = f"data/{season}/news/{gameweek}/{collection}"
    for layer in (EXTRACTED_LAYER, *LEGACY_EXTRACTED_LAYERS):
        candidate = f"{base}/{layer}"
        if os.path.isdir(candidate):
            if layer != EXTRACTED_LAYER:
                logger.info(
                    "GW%s %s: reading the retired '%s' layer; re-run the extractor to write '%s'.",
                    gameweek, collection, layer, EXTRACTED_LAYER,
                )
            return candidate
    return None

def load_bootstrap_players(season: str = SEASON) -> dict[int, str]:
    """Load player ID map {id: web_name} from bootstrap."""
    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/bootstrap"))
    snapshot = store.load_latest()
    if not snapshot or "elements" not in snapshot:
        raise ValueError("Bootstrap snapshot missing elements.")
    
    return {p["id"]: p["web_name"] for p in snapshot["elements"]}

def process_extracted_article(
    season: str,
    gameweek: int,
    collection: str,
    article_id: str,
    player_map: dict[int, str]
):
    """Read one article's extraction, validate every player identity, write the facts layer.

    Path: `data/{season}/news/{gw}/{collection}/{EXTRACTED_LAYER}/{article_id}`, falling back to a
    retired layer when only that exists.

    Returns:
    - `(facts_seen, facts_rejected)`, so the caller can judge the rejection *rate* across a
      gameweek. One wrong id is the extractor being a language model; a quarter of them wrong is a
      systemic mismatch and `main` fails on it.
    """
    directory = _extracted_dir(season, gameweek, collection)
    if directory is None:
        logger.warning(f"No extraction layer for GW{gameweek} {collection}")
        return (0, 0)
    extracted_base_path = f"{directory}/{article_id}"
    extracted_store = JsonSnapshotStore(SnapshotSpec(base_path=extracted_base_path))

    try:
        extracted_data = extracted_store.load_latest()
    except Exception:
        logger.warning(f"No extraction found for article {article_id} at {extracted_base_path}")
        return (0, 0)

    raw_facts = extracted_data.get("facts", [])
    if not raw_facts:
        logger.info(f"No facts extracted for article {article_id}")
        return (0, 0)

    validated_facts: List[NewsFact] = []
    rejected = 0

    # 2. Validate. A rejected fact is dropped and logged; see the module doc for why the run does
    #    not abort on one.
    for item in raw_facts:
        pid = item.get("player_id")
        web_name = item.get("web_name")

        if pid not in player_map:
            logger.warning(
                "Rejected: article %s names player id %r, which is not in the %s squad list.",
                article_id, pid, season,
            )
            rejected += 1
            continue

        system_name = player_map[pid]
        if comparable_name(web_name or '') != comparable_name(system_name):
            logger.warning(
                "Rejected: article %s says id %s is '%s', the game says '%s'. The id and the name "
                "disagree about who this is; a spelling difference alone would have been forgiven.",
                article_id, pid, web_name, system_name,
            )
            rejected += 1
            continue

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

    if rejected:
        logger.warning(
            "Article %s: %d of %d extracted fact(s) rejected, %d kept.",
            article_id, rejected, len(raw_facts), len(validated_facts),
        )
    if not validated_facts:
        return (len(raw_facts), rejected)

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
    return (len(raw_facts), rejected)

def list_extracted_articles(season: str, gameweek: int, collection: str) -> List[str]:
    """List article IDs present in the extraction layer for a gameweek."""
    directory = _extracted_dir(season, gameweek, collection)
    if directory is None:
        return []

    # Discover all article stores in the directory
    stores = JsonSnapshotStore.discover_stores(directory)
    
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
    total_facts = 0
    total_rejected = 0
    
    for gw in range(first_gw, last_gw + 1):
        logger.info(f"Processing GW {gw}...")
        
        if args.article_id:
            target_ids = [str(aid) for aid in args.article_id]
        else:
            target_ids = list_extracted_articles(SEASON, gw, args.news_collection)
            
        for article_id in target_ids:
            try:
                seen, rejected = process_extracted_article(
                    season=SEASON,
                    gameweek=gw,
                    collection=args.news_collection,
                    article_id=article_id,
                    player_map=player_map
                )
                total_processed += 1
                total_facts += seen
                total_rejected += rejected
            except Exception as e:
                logger.error(f"Error processing article {article_id} in GW {gw}: {e}")
                raise e

    kept = total_facts - total_rejected
    logger.info(
        "Finished. %d article(s), %d fact(s) kept, %d rejected.",
        total_processed, kept, total_rejected,
    )
    if total_facts and total_rejected / total_facts > MAX_REJECT_RATE:
        raise ValueError(
            f"{total_rejected} of {total_facts} extracted facts failed identity validation "
            f"({total_rejected / total_facts:.0%}, limit {MAX_REJECT_RATE:.0%}). That is too many "
            f"to be ordinary extraction error: check the bootstrap snapshot is this season's "
            f"(./run.sh -m src.fpl.fetch) before trusting anything in the facts layer."
        )

if __name__ == "__main__":
    main()
