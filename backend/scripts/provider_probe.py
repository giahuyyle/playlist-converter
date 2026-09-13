"""Read-only provider experiments using an account connected through the app.

Example: python -m scripts.provider_probe USER_ID youtube PLAYLIST_ID --destination apple
Does not create or modify provider playlists. Use the review UI for writes.
"""

import argparse
import json

from app.core.database import Session
from app.providers import provider_for
from app.services.matching import rank


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id")
    parser.add_argument("provider", choices=["youtube", "apple", "spotify"])
    parser.add_argument("playlist_id", nargs="?")
    parser.add_argument("--destination", choices=["youtube", "apple", "spotify"])
    args = parser.parse_args()
    with Session() as db, provider_for(db, args.user_id, args.provider) as provider:
        if not args.playlist_id:
            print(json.dumps(provider.get_playlists(), ensure_ascii=False, indent=2))
            return
        print(json.dumps(provider.get_playlist(args.playlist_id), ensure_ascii=False, indent=2))
        tracks = provider.get_playlist_tracks(args.playlist_id)
        print(json.dumps([t.model_dump() for t in tracks], ensure_ascii=False, indent=2))
        if args.destination and tracks:
            with provider_for(db, args.user_id, args.destination) as destination:
                print(
                    json.dumps(
                        rank(tracks[0], destination.search_tracks(tracks[0])),
                        ensure_ascii=False,
                        indent=2,
                    )
                )


if __name__ == "__main__":
    main()
