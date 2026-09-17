"""LLM news processor: turns Scout articles into per-player facts.

Reads the `raw` layer, asks an LLM what each article says about specific players, and writes the
answer to the `extracted` layer for `src/fpl/loader/news/validate.py` to check.

The model is reached through `claude -p` (`src/fpl/client/claude_cli.py`), which replaced a Gemini
SDK client and its `GEMINI_API_KEY`. Two consequences worth knowing before editing this file: the
CLI has no server-side JSON-schema mode, so `RESPONSE_SCHEMA` instructs the prompt and is checked
after the fact; and the call runs in an empty directory with tools disabled, because `claude -p`
started inside this repo reads `CLAUDE.md` and behaves like an engineering agent rather than an
extractor.

Nothing here judges whether a fact is *true* - only that an article said it. Player identity is
checked in the validation layer, which is why that layer exists separately.
"""
import argparse
import asyncio
import logging

from src.fpl.client.claude_cli import DEFAULT_MODEL, ClaudeCliClient
from src.fpl.loader.news.pl import list_saved_news, SEASON
from src.fpl.models.immutable import NewsModel
from src.fpl.loader.store.json import JsonSnapshotStore, SnapshotSpec

EXTRACTED_LAYER = 'extracted'
"""Layer the LLM writes to. Named for what it holds, not who produced it: it was `gemini` until
the extractor changed, and a directory named after a retired vendor is a lie that survives."""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Instructed in the prompt and checked on the way back - the CLI has no structured-output mode.
RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "player_id": {"type": "integer"},
            "web_name": {"type": "string"},
            "fact": {"type": "string"},
            "form": {"type": "number"},
            "availability": {"type": "number"}
        },
        "required": ["player_id", "web_name", "fact", "form", "availability"]
    }
}

STATIC_RULES = """
FPL RULES & CONTEXT:

1. SCORING SUMMARY:
- Minutes: 1pt for playing, 2pts for 60+ mins.
- Goals: FWD (4), MID (5), DEF/GK (6).
- Assists: 3pts.
- Clean Sheet: DEF/GK (4), MID (1).
- Saves: 1pt per 3 saves.
- Penalties: -2 for miss, +5 for save.
- Cards: Yellow (-1), Red (-3).
- Bonus: 1-3 pts for best players in match based on BPS.
- Captain: Double points (Regular only, Draft has no captains).

2. DEFENSIVE CONTRIBUTION (New Rule):
- Defenders: +2 pts for 10+ combined clearances, blocks, interceptions (CBI) and tackles.
- Mid/Fwd: +2 pts for 12+ combined CBI, tackles, and recoveries.
- This rewards active defensive players even if they don't get clean sheets.

3. AUTOMATIC SUBSTITUTIONS & STABILITY:
- If a starting player doesn't play (0 minutes), they are auto-replaced by the highest priority bench player who preserves a valid formation.
- STRATEGY: It is crucial to pick "stable" players who are likely to start. A player who is benched in real life (scoring 0 or 1 point) blocks a potentially high-scoring player from coming off your fantasy bench.
- "Availability" checks are vital to avoid this blocking scenario.

4. LEAGUE TYPES & STRATEGY:
- Regular (Classic) League: Managers buy players within a budget. Focus is on LONG-TERM value and consistency.
- Draft League: No budget, unique players (owned by only one manager). Waiver wire is used for transactions. Focus can often be on SHORT-TERM "punts" or specific matchups, as you can churn the squad more easily via waivers.
- When extracting facts, note if a player is a good long-term asset (Regular) or a specific short-term target (Draft/Streamer).
"""

def get_players_context(season: str = SEASON) -> str:
    """Load all players from bootstrap and format for prompt."""
    store = JsonSnapshotStore(SnapshotSpec(base_path=f"data/{season}/bootstrap"))
    snapshot = store.load_latest()
    if not snapshot or "elements" not in snapshot:
        raise ValueError("Bootstrap snapshot missing elements.")
        
    elements = snapshot["elements"]
    teams = {t["id"]: t["short_name"] for t in snapshot.get("teams", [])}
    
    context_lines = []
    for p in elements:
        team_name = teams.get(p["team"], "UNK")
        # Format: ID: Name (Team) - Price
        context_lines.append(f"{p['id']}: {p['web_name']} ({team_name}) - £{p['now_cost']/10}m")
        
    return "\n".join(context_lines)

async def process_article(
    client: ClaudeCliClient,
    article: NewsModel, 
    rules_context: str, 
    players_context: str,
    season: str = SEASON
):
    """Extract facts from one article and store them in the `extracted` layer."""

    # Path: data/{season}/news/{gw}/{collection}/extracted/{article_id}
    base_path = f"data/{season}/news/{article.gameweek}/{article.collection}/{EXTRACTED_LAYER}/{article.id}"
    store = JsonSnapshotStore(SnapshotSpec(base_path=base_path))
    
    async def fetch_from_llm() -> dict:
        prompt = f"""
You are an FPL Expert and Fact Extractor.
Your task is to extract structured facts about player availability, form, and strategic value from the provided news article.
Fact is what is known about the player from the article.
Form is how likely the player is to score decent points next match given he will have enough minutes.
Availability is how likely the player is to get enough minutes to realize his form. Usually is means to start next match.

CONTEXT:
{rules_context}

PLAYERS (ID: Name (Team) - Price):
{players_context}

ARTICLE:
Title: {article.title}
Date: {article.date}
Summary: {article.summary}
Body:
{article.body}

INSTRUCTIONS:
1. Extract facts relevant to specific players found in the PLAYERS list.
2. Output a JSON array of objects.
3. For each fact, identify the 'player_id' and 'web_name' exactly as they appear in the PLAYERS list.
4. 'fact': A concise summary of the essential info. 
   - Combine multiple related facts into a single clear summary. 
   - Focus on stats, recent performance, and injury/fitness news.
   - Explicitly mention if the text suggests the player is a long-term hold (Regular focus) or a short-term punt (Draft focus).
5. 'form': Estimate impact on player form, range [-1, 1].
   - -1: Certain evidence the player will score less points next match (than in recent matches).
   - +1: Strong evidence the player will score higher than average points next match (either keep or start scoring high).
   - If you estimate "form" such as abs(form) > 0.5, you must make sure that "fact" contains evidence of this form.
   - Be conservative: close to 0 if unsure. Article authors are usually optimistic, so we need to be more realistic.
6. 'availability': Estimate impact on player ability to get enough minutes to realize his form, range [-1, 1].
   - -1.0: Confirmed OUT (injury, suspension, personal issues, etc.).
   - -0.5: Doubtful/Touch-and-go.
   - -1..0: Has a direct position rival teammate in a comparable form, so this teammate can steal his minutes.
   - 0.0: No news, or player is available but no specific update. (DEFAULT)
   - 0..+1.0: Explicit confirmation of return to fitness/starting XI.
   - If you estimate "availability" such as abs(availability) > 0.5, you must make sure that "fact" contains evidence of this availability.
   - CRITICAL: High availability score means the player is SAFE to start (avoids auto-sub blocking). Good form does NOT mean high availability. Although a player can get off the bench and score points, article authors usually focus on starting players.

OUTPUT FORMAT:
JSON Array of objects with keys: player_id, web_name, fact, form, availability.
"""
        logger.info("Extracting facts from article %s via claude -p...", article.id)
        result = await client.generate_content(prompt, response_schema=RESPONSE_SCHEMA)
        logger.info("Article %s: %d fact(s) extracted.", article.id, len(result))
        return {"facts": result, "model": client.model, "extractor": "claude-cli"}

    # Use get_or_fetch to handle caching
    # Freshness is set to very high number (e.g. 365 days) because once processed, the extraction for a static article shouldn't change much
    # unless code changes. But here we rely on the fact that if it exists, we use it. 
    # If we want to force update, we'd need a flag, but for now we trust the cache.
    # The requirement says "the cache will decide this".
    await store.get_or_fetch(freshness=0, fetch_fn=fetch_from_llm)

async def main_async(args):
    client = ClaudeCliClient(model=args.model)
    
    logger.info("Loading context...")
    rules_context = STATIC_RULES
    players_context = get_players_context()
    
    # Determine gameweek range
    first_gw = args.first_gw if args.first_gw is not None else args.last_gw
    last_gw = args.last_gw
    
    processed_count = 0
    
    for gw in range(first_gw, last_gw + 1):
        logger.info(f"Listing news for GW {gw}...")
        articles = list_saved_news(
            collection=args.news_collection,
            gameweek=gw,
            include_body=True, # We need the body for LLM processing
            tag_whitelist=args.tag_id
        )
        
        logger.info(f"Found {len(articles)} articles for GW {gw}.")
        
        for article in articles:
            if args.article_id and article.id not in args.article_id:
                continue
            try:
                await process_article(client, article, rules_context, players_context)
                processed_count += 1
            except Exception as e:
                logger.exception(f"Failed to process article {article.id}")
                raise e
                
    client.close()
    logger.info(f"Finished. Processed {processed_count} articles.")

def main():
    parser = argparse.ArgumentParser(description="Process news with LLM to extract facts.")
    parser.add_argument(
        "news_collection",
        help="News collection to use (e.g., 'fpl_scout')",
    )
    parser.add_argument("--first-gw", type=int, default=None, help="Lower bound for gameweek window")
    parser.add_argument("--last-gw", type=int, required=True, help="Upper bound for gameweek window (required)")
    parser.add_argument(
        "--tag-id", type=int, action="append", required=False,
        help="Filter by tag ID",
    )
    parser.add_argument("--article-id", type=int, action="append", help="Filter by article ID")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Model for `claude -p`. Default {DEFAULT_MODEL}.",
    )
    
    args = parser.parse_args()
    
    asyncio.run(main_async(args))

if __name__ == "__main__":
    main()

