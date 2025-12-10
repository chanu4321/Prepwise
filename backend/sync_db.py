import os
import sys

# Add project root to path BEFORE importing backend modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.database import get_db_connection

def sync_database():
    """Removes DB entries for files that no longer exist on disk."""
    print("Syncing Database with Filesystem...")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get all papers from DB
    cur.execute("SELECT id, file_path, filename FROM papers")
    papers = cur.fetchall()
    
    deleted_count = 0
    
    for paper in papers:
        paper_id, file_path, filename = paper
        
        # Check if file exists
        if not os.path.exists(file_path):
            print(f"File missing: {filename} (ID: {paper_id}). Removing from DB...")
            cur.execute("DELETE FROM papers WHERE id = %s", (paper_id,))
            deleted_count += 1
            
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"Sync Complete. Removed {deleted_count} orphaned entries.")

if __name__ == "__main__":
    sync_database()
