import argparse
import os
import sys
import json
import getpass
import time
from datetime import datetime

# Third-party imports
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    from git import Repo, Actor
except ImportError:
    print("Missing dependencies. Please run: pip install spotipy gitpython")
    sys.exit(1)

def parse_credentials(file_path):
    """Parses the username from the specified credentials file."""
    if not os.path.exists(file_path):
        print(f"Error: Credentials file '{file_path}' not found.")
        sys.exit(1)
        
    username = None
    with open(file_path, 'r') as f:
        for line in f:
            if line.strip().startswith('user='):
                username = line.strip().split('=', 1)[1]
                break
    
    if not username:
        print("Error: Could not find 'user=' in credentials file.")
        sys.exit(1)
        
    return username

def get_audio_features_batched(sp, track_ids):
    """Fetches audio features in batches of 100 (Spotify API limit)."""
    features = []
    # Spotify allows max 100 ids per request
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i + 100]
        try:
            batch_features = sp.audio_features(batch)
            features.extend(batch_features)
        except Exception as e:
            print(f"Warning: Failed to fetch audio features for batch: {e}")
            # Append None for failed batch to keep alignment, or handle gracefully
            features.extend([None] * len(batch))
    return features

def sanitize_filename(name):
    """Sanitizes playlist names for file system usage."""
    return "".join([c for c in name if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()

def run_backup(username, client_id, client_secret):
    # 1. Setup Spotify Connection
    # We use a broad scope to ensure we get private playlists and library
    scope = "playlist-read-private playlist-read-collaborative user-library-read"
    
    redirect_uri = "http://localhost:8888/callback"
    
    print(f"Authenticating as {username}...")
    
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        username=username,
        open_browser=True
    ))

    # 2. Fetch Playlists
    print("Fetching playlists...")
    playlists = []
    results = sp.current_user_playlists()
    playlists.extend(results['items'])
    
    while results['next']:
        results = sp.next(results)
        playlists.extend(results['items'])

    print(f"Found {len(playlists)} playlists. Starting backup...")

    # Directory for JSON files
    backup_dir = "playlist_data"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)

    # 3. Process each playlist
    for pl in playlists:
        if not pl: continue # Skip empty objects
        
        pl_name = pl['name']
        pl_id = pl['id']
        owner = pl['owner']['display_name']
        
        print(f"Processing: {pl_name} (by {owner})")
        
        # Get all tracks for this playlist
        tracks = []
        track_results = sp.playlist_items(pl_id, additional_types=['track'])
        tracks.extend(track_results['items'])
        
        while track_results['next']:
            track_results = sp.next(track_results)
            tracks.extend(track_results['items'])

        # Prepare data structure
        playlist_data = {
            "name": pl_name,
            "id": pl_id,
            "description": pl['description'],
            "owner": owner,
            "snapshot_id": pl['snapshot_id'],
            "total_tracks": len(tracks),
            "tracks": []
        }

        # Collect Track IDs for Audio Features
        track_ids = []
        clean_tracks = [] # Temp list to hold track objects before merging features
        
        for item in tracks:
            if not item or not item.get('track'): continue
            
            track = item['track']
            if track['id'] is None: continue # Skip local files/invalid tracks
            
            track_ids.append(track['id'])
            
            # Basic Metadata
            meta = {
                "title": track['name'],
                "artist": ", ".join([artist['name'] for artist in track['artists']]),
                "album": track['album']['name'],
                "length_ms": track['duration_ms'],
                "length_formatted": time.strftime('%M:%S', time.gmtime(track['duration_ms']/1000)),
                "spotify_url": track['external_urls'].get('spotify'),
                "id": track['id'],
                "added_at": item['added_at']
            }
            clean_tracks.append(meta)

        # Fetch Audio Features (BPM, Key, etc)
        if track_ids:
            audio_feats = get_audio_features_batched(sp, track_ids)
            
            # Merge features into track data
            # audio_feats list corresponds to track_ids list index-wise
            feature_map = {f['id']: f for f in audio_feats if f}
            
            for t in clean_tracks:
                f_data = feature_map.get(t['id'])
                if f_data:
                    t['bpm'] = f_data.get('tempo')
                    t['key'] = f_data.get('key')
                    t['mode'] = f_data.get('mode') # 0 = Minor, 1 = Major
                    t['time_signature'] = f_data.get('time_signature')
                    t['danceability'] = f_data.get('danceability')
                    t['energy'] = f_data.get('energy')
                else:
                    t['bpm'] = None
                    t['key'] = None
        
        playlist_data['tracks'] = clean_tracks

        # Write to file
        safe_name = sanitize_filename(f"{pl_name}_{owner}")
        file_path = os.path.join(backup_dir, f"{safe_name}.json")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(playlist_data, f, indent=2, ensure_ascii=False)

    # 4. Git Operations
    print("Performing Git operations...")
    repo_dir = os.getcwd()
    
    try:
        repo = Repo(repo_dir)
    except Exception:
        print("Initializing new Git repository...")
        repo = Repo.init(repo_dir)

    # Add changes
    repo.git.add(backup_dir)
    # Also add the script itself so the logic is backed up
    repo.git.add(os.path.basename(__file__))

    if repo.is_dirty():
        print("Changes detected. Committing...")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        repo.index.commit(f"Backup: Playlist update {timestamp}")
        print("Backup committed successfully.")
    else:
        print("No changes detected in playlists since last backup.")

def main():
    creds_file='credentials.txt'
    username = None
    client_id = None
    client_secret = None
    try:
        with open(creds_file, 'r') as file:
            username = file.readline().strip()
            client_id = file.readline().strip()
            client_secret = file.readline().strip()
    except FileNotFoundError:
        print(f"{creds_file} not found for Spotify credentials.")
    except IOError:
        print(f"{creds_file} is malformed. Should be three lines - email, client ID, followed by client secret.")
    
    if client_id is None or client_secret is None:
        # Get API Credentials (Interactive)
        # Spotify API requires Client ID/Secret. The user logs in via browser.
        print("Please enter your Spotify App Credentials.")
        print("(These can be found at https://developer.spotify.com/dashboard)")
    
        # Check if they are in env vars first for convenience, otherwise prompt
        client_id = os.environ.get("SPOTIPY_CLIENT_ID")
        if not client_id:
            client_id = input("Client ID: ").strip()
        
        client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
        if not client_secret:
            client_secret = input("Client Secret (hidden): ").strip()

        username = os.environ.get("SPOTIFY_EMAIL")
        if not username:
            username = input("Email: ").strip()

    if not client_id or not client_secret or not username:
        print("Error: Username, Client ID, and Secret are required.")
        sys.exit(1)
    
    run_backup(username, client_id, client_secret)

if __name__ == "__main__":
    main()
