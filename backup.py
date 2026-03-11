#!/usr/bin/env python3
import argparse
import os
import sys
import json
import getpass
import time
from datetime import datetime

BATCH_SIZE=100

# Third-party imports
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    from git import Repo
except ImportError:
    print("Missing dependencies. Please run: pip install spotipy gitpython")
    sys.exit(1)

def parse_credentials(file_path):
    """Parses username, client_id, and client_secret from the credentials file."""
    if not os.path.exists(file_path):
        print(f"Error: Credentials file '{file_path}' not found.")
        sys.exit(1)
        
    creds = {"user": None, "id": None, "secret": None}
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if 'user=' in line:
                creds["user"] = line.split('user=')[-1].strip()
            elif 'client=' in line:
                creds["id"] = line.split('client=')[-1].strip()
            elif 'secret=' in line:
                creds["secret"] = line.split('secret=')[-1].strip()
                
    if not all([creds["user"], creds["id"], creds["secret"]]):
        print("Error: Credentials file must contain user=, id=, and secret=")
        sys.exit(1)
    return creds

def get_audio_features_batched(sp, track_ids):
    """Fetches audio features in batches of 100 with error handling for 403s."""
    features = []
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i + 100]
        try:
            batch_features = sp.audio_features(batch)
            # Filter out None values returned by Spotify for specific tracks
            features.extend(batch_features if batch_features else [None] * len(batch))
        except Exception as e:
            # If 403 occurs, we skip this batch's features but keep the metadata
            print(f"  ! Skipping audio features for this batch (Access Denied/403)")
            features.extend([None] * len(batch))
    return features

def sanitize_filename(name):
    """Sanitizes playlist names for file system usage."""
    return "".join([c for c in name if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()

def run_backup(username, client_id, client_secret, make_commit):
    redirect_uri = "http://127.0.0.1:8888/callback"
    scope = "playlist-read-private playlist-read-collaborative user-library-read"
    
    stats = {"playlists": 0, "tracks": 0, "bpm_success": 0, "bpm_fail": 0}
    cache_path = f".cache-{creds['user']}"
    
    # Force fresh login to ensure the local server trigger is tested
    if os.path.exists(cache_path):
        os.remove(cache_path)
    
    print(f"Authenticating as {username}...")
    
    auth_manager = SpotifyOAuth(
        client_id=creds['id'],
        client_secret=creds['secret'],
        redirect_uri=redirect_uri,
        scope=scope,
        username=creds['user'],
        cache_path=cache_path,
        open_browser=True # This opens the browser automatically
    )

    sp = spotipy.Spotify(auth_manager=auth_manager, retries=5)

    print(f"--- Authenticating {creds['user']} ---")
    print("A browser window should open. Once you authorize, the script will continue automatically.")

    print("Fetching playlists...")
    playlists = []
    try:
        results = sp.current_user_playlists()
        playlists.extend(results['items'])
        while results['next']:
            results = sp.next(results)
            playlists.extend(results['items'])
    except Exception as e:
        print(f"Failed to fetch playlists: {e}")
        return

    print(f"Found {len(playlists)} playlists. Starting backup...")

    backup_dir = "playlist_data"
    os.makedirs(backup_dir, exist_ok=True)

    for pl in playlists:
        if not pl: continue 
        pl_name = pl['name']
        pl_id = pl['id']
        owner = pl['owner']['display_name']
        
        print(f"Processing: {pl_name}")
        
        tracks = []
        try:
            track_results = sp.playlist_items(pl_id, additional_types=['track'])
            tracks.extend(track_results['items'])
            while track_results['next']:
                track_results = sp.next(track_results)
                tracks.extend(track_results['items'])
        except Exception as e:
            print(f"  ! Could not access tracks for {pl_name}: {e}")
            continue

        playlist_data = {
            "name": pl_name,
            "id": pl_id,
            "owner": owner,
            "snapshot_id": pl['snapshot_id'],
            "total_tracks": len(tracks),
            "tracks": []
        }

        track_ids = []
        clean_tracks = [] 
        
        for item in tracks:
            if not item or not item.get('track'): continue
            track = item['track']
            if not track.get('id'): continue 
            
            track_ids.append(track['id'])
            clean_tracks.append({
                "title": track.get('name', 'Unknown'),
                "artist": ", ".join([a['name'] for a in track.get('artists', [])]),
                "album": track.get('album', {}).get('name', 'Unknown'),
                "length_ms": track.get('duration_ms', 0),
                "spotify_url": track.get('external_urls', {}).get('spotify'),
                "id": track['id'],
                "added_at": item.get('added_at')
            })
        
        playlist_data['tracks'] = clean_tracks
        safe_name = sanitize_filename(f"{pl_name}_{owner}")
        file_path = os.path.join(backup_dir, f"{safe_name}.json")

        stats["playlists"] += 1
        stats["tracks"] += len(playlist_tracks)
        print(f"({len(playlist_tracks)}.tracks)")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(playlist_data, f, indent=2, ensure_ascii=False)

    if make_commit:
        # Git Operations
        print("Performing Git operations...")
        repo_dir = os.getcwd()
        repo = Repo(repo_dir)

        repo.git.add(backup_dir)

        if repo.is_dirty(untracked_files=True):
            print("Changes detected. Committing...")
            repo.index.commit(f"Backup: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("Backup committed.")
        else:
            print("No changes detected.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--credentials", default="credentials.txt", required=False, help="Provide credentials file with user= client= and secret= lines.")
    parser.add_argument("-n", "--no-commit", action="store_true", help="Don't make any git commits.")
    args = parser.parse_args()

    creds = parse_credentials(args.credentials)
    make_commit = not args.no_commit
    
    # Prompt only if missing from file
    client_id = creds["id"] or input("Client ID: ").strip()
    client_secret = creds["secret"] or getpass.getpass("Client Secret: ").strip()

    run_backup(creds["user"], client_id, client_secret, make_commit)

if __name__ == "__main__":
    main()
