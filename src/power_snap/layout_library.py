"""Layout library management for PowerSnap

Manages a global library of named zone layouts and workspace-to-layout mappings.
"""

import json
import os
from typing import List, Optional, Dict
from datetime import datetime
from .zone import Zone


class Layout:
    """Represents a named zone layout"""

    def __init__(self, name: str, zones: List[Zone], description: str = "",
                 created_date: Optional[str] = None, modified_date: Optional[str] = None):
        self.name = name
        self.zones = zones
        self.description = description
        self.created_date = created_date or datetime.now().isoformat()
        self.modified_date = modified_date or self.created_date

    def to_dict(self) -> Dict:
        """Convert layout to dictionary for JSON serialization"""
        return {
            "name": self.name,
            "description": self.description,
            "created_date": self.created_date,
            "modified_date": self.modified_date,
            "zones": [zone.to_dict() for zone in self.zones]
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Layout':
        """Create layout from dictionary"""
        zones = [Zone.from_dict(z) for z in data.get("zones", [])]
        return cls(
            name=data["name"],
            zones=zones,
            description=data.get("description", ""),
            created_date=data.get("created_date"),
            modified_date=data.get("modified_date")
        )

    def update_zones(self, zones: List[Zone]):
        """Update zones and modification date"""
        self.zones = zones
        self.modified_date = datetime.now().isoformat()

    def __repr__(self) -> str:
        return f"Layout('{self.name}', {len(self.zones)} zones)"


class LayoutLibrary:
    """Manages a library of named zone layouts"""

    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize layout library

        Args:
            config_dir: Directory to store layouts. Defaults to ~/.config/powersnap
        """
        if config_dir is None:
            config_dir = os.path.expanduser("~/.config/powersnap")

        self.config_dir = config_dir
        self.layouts_dir = os.path.join(config_dir, "layouts")
        self.workspace_mapping_file = os.path.join(config_dir, "workspace_layouts.json")

        # Create directories if they don't exist
        os.makedirs(self.layouts_dir, exist_ok=True)

        # Cache of loaded layouts
        self._layouts_cache: Dict[str, Layout] = {}
        self._workspace_mappings: Dict[int, str] = {}

        # Load workspace mappings
        self._load_workspace_mappings()

    def create_layout(self, name: str, zones: List[Zone], description: str = "") -> Layout:
        """
        Create a new layout

        Args:
            name: Layout name (used as filename)
            zones: List of zones in the layout
            description: Optional description

        Returns:
            Created Layout object
        """
        layout = Layout(name, zones, description)
        self.save_layout(layout)
        self._layouts_cache[name] = layout
        return layout

    def save_layout(self, layout: Layout) -> bool:
        """
        Save layout to disk

        Args:
            layout: Layout to save

        Returns:
            True if successful, False otherwise
        """
        try:
            filepath = self._get_layout_filepath(layout.name)

            # Update modification date
            layout.modified_date = datetime.now().isoformat()

            with open(filepath, 'w') as f:
                json.dump(layout.to_dict(), f, indent=2)

            # Update cache
            self._layouts_cache[layout.name] = layout

            return True

        except Exception as e:
            print(f"Error saving layout '{layout.name}': {e}")
            return False

    def load_layout(self, name: str, force_reload: bool = True) -> Optional[Layout]:
        """
        Load layout by name

        Args:
            name: Layout name
            force_reload: Always reload from disk (default: True to avoid stale cache)

        Returns:
            Layout object or None if not found
        """
        # Always reload from disk by default to avoid stale cache
        # (zone editor runs in separate process, so cache can be outdated)
        if not force_reload and name in self._layouts_cache:
            return self._layouts_cache[name]

        try:
            filepath = self._get_layout_filepath(name)

            if not os.path.exists(filepath):
                return None

            with open(filepath, 'r') as f:
                data = json.load(f)

            layout = Layout.from_dict(data)
            self._layouts_cache[name] = layout

            return layout

        except Exception as e:
            print(f"Error loading layout '{name}': {e}")
            return None

    def rename_layout(self, old_name: str, new_name: str) -> bool:
        """
        Rename a layout

        Args:
            old_name: Current layout name
            new_name: New layout name

        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if old layout exists
            if not self.layout_exists(old_name):
                print(f"Layout '{old_name}' does not exist")
                return False

            # Check if new name already exists
            if self.layout_exists(new_name):
                print(f"Layout '{new_name}' already exists")
                return False

            # Load the layout
            layout = self.load_layout(old_name)
            if not layout:
                return False

            # Update the layout name
            layout.name = new_name
            layout.modified_date = datetime.now().isoformat()

            # Save with new name
            new_filepath = self._get_layout_filepath(new_name)
            with open(new_filepath, 'w') as f:
                json.dump(layout.to_dict(), f, indent=2)

            # Delete old file
            old_filepath = self._get_layout_filepath(old_name)
            if os.path.exists(old_filepath):
                os.remove(old_filepath)

            # Update cache
            if old_name in self._layouts_cache:
                del self._layouts_cache[old_name]
            self._layouts_cache[new_name] = layout

            # Update workspace mappings
            workspaces_to_update = [ws for ws, layout_name in self._workspace_mappings.items()
                                   if layout_name == old_name]
            for ws in workspaces_to_update:
                self._workspace_mappings[ws] = new_name

            if workspaces_to_update:
                self._save_workspace_mappings()

            return True

        except Exception as e:
            print(f"Error renaming layout '{old_name}' to '{new_name}': {e}")
            return False

    def delete_layout(self, name: str) -> bool:
        """
        Delete a layout

        Args:
            name: Layout name

        Returns:
            True if successful, False otherwise
        """
        try:
            filepath = self._get_layout_filepath(name)

            if os.path.exists(filepath):
                os.remove(filepath)

            # Remove from cache
            if name in self._layouts_cache:
                del self._layouts_cache[name]

            # Remove from workspace mappings
            workspaces_to_update = [ws for ws, layout_name in self._workspace_mappings.items()
                                   if layout_name == name]
            for ws in workspaces_to_update:
                del self._workspace_mappings[ws]

            if workspaces_to_update:
                self._save_workspace_mappings()

            return True

        except Exception as e:
            print(f"Error deleting layout '{name}': {e}")
            return False

    def list_layouts(self) -> List[str]:
        """
        List all available layout names

        Returns:
            List of layout names
        """
        try:
            if not os.path.exists(self.layouts_dir):
                return []

            layout_files = [f for f in os.listdir(self.layouts_dir)
                          if f.endswith('.json')]

            return [os.path.splitext(f)[0] for f in sorted(layout_files)]

        except Exception as e:
            print(f"Error listing layouts: {e}")
            return []

    def get_all_layouts(self) -> List[Layout]:
        """
        Load all layouts

        Returns:
            List of Layout objects
        """
        layouts = []
        for name in self.list_layouts():
            layout = self.load_layout(name)
            if layout:
                layouts.append(layout)
        return layouts

    def layout_exists(self, name: str) -> bool:
        """Check if a layout exists"""
        return os.path.exists(self._get_layout_filepath(name))

    def get_active_layout(self, workspace_id: int) -> Optional[str]:
        """
        Get the active layout name for a workspace

        Args:
            workspace_id: Workspace ID

        Returns:
            Layout name or None if no mapping exists
        """
        # Reload mappings from disk to get latest changes
        self._load_workspace_mappings()
        return self._workspace_mappings.get(workspace_id)

    def set_active_layout(self, workspace_id: int, layout_name: str) -> bool:
        """
        Set the active layout for a workspace

        Args:
            workspace_id: Workspace ID
            layout_name: Layout name to assign

        Returns:
            True if successful, False otherwise
        """
        # Verify layout exists
        if not self.layout_exists(layout_name):
            print(f"Layout '{layout_name}' does not exist")
            return False

        self._workspace_mappings[workspace_id] = layout_name
        return self._save_workspace_mappings()

    def get_workspace_layout(self, workspace_id: int) -> Optional[Layout]:
        """
        Get the active layout for a workspace (loads the layout object)

        Args:
            workspace_id: Workspace ID

        Returns:
            Layout object or None if no mapping exists or layout not found
        """
        layout_name = self.get_active_layout(workspace_id)

        if layout_name is None:
            # Try "default" layout
            layout_name = "default"

        return self.load_layout(layout_name)

    def _get_layout_filepath(self, name: str) -> str:
        """Get filepath for a layout"""
        # Sanitize name to prevent directory traversal
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
        return os.path.join(self.layouts_dir, f"{safe_name}.json")

    def _load_workspace_mappings(self) -> bool:
        """Load workspace-to-layout mappings from disk"""
        try:
            if not os.path.exists(self.workspace_mapping_file):
                self._workspace_mappings = {}
                return True

            with open(self.workspace_mapping_file, 'r') as f:
                data = json.load(f)

            # Convert string keys back to integers
            self._workspace_mappings = {int(k): v for k, v in data.items()}

            return True

        except Exception as e:
            print(f"Error loading workspace mappings: {e}")
            self._workspace_mappings = {}
            return False

    def _save_workspace_mappings(self) -> bool:
        """Save workspace-to-layout mappings to disk"""
        try:
            # Convert integer keys to strings for JSON
            data = {str(k): v for k, v in self._workspace_mappings.items()}

            with open(self.workspace_mapping_file, 'w') as f:
                json.dump(data, f, indent=2)

            return True

        except Exception as e:
            print(f"Error saving workspace mappings: {e}")
            return False



def main():
    """Command-line interface for layout library management"""
    import argparse

    parser = argparse.ArgumentParser(description='PowerSnap Layout Library Manager')
    parser.add_argument('--list', action='store_true', help='List all layouts')
    parser.add_argument('--delete', metavar='NAME', help='Delete a layout')
    parser.add_argument('--show', metavar='NAME', help='Show layout details')
    parser.add_argument('--set-workspace', nargs=2, metavar=('WORKSPACE', 'LAYOUT'),
                       help='Assign layout to workspace')
    parser.add_argument('--list-workspaces', action='store_true',
                       help='List workspace-to-layout mappings')

    args = parser.parse_args()

    library = LayoutLibrary()

    if args.list:
        layouts = library.list_layouts()
        if layouts:
            print(f"Available layouts ({len(layouts)}):")
            print("-" * 60)
            for name in layouts:
                layout = library.load_layout(name)
                if layout:
                    print(f"  {name:20} - {len(layout.zones)} zones")
                    if layout.description:
                        print(f"    {layout.description}")
        else:
            print("No layouts found.")

    elif args.show:
        layout = library.load_layout(args.show)
        if layout:
            print(f"Layout: {layout.name}")
            print(f"Description: {layout.description}")
            print(f"Created: {layout.created_date}")
            print(f"Modified: {layout.modified_date}")
            print(f"Zones: {len(layout.zones)}")
            print("-" * 60)
            for i, zone in enumerate(layout.zones, 1):
                print(f"  {i}. {zone}")
        else:
            print(f"Layout '{args.show}' not found.")

    elif args.delete:
        if library.delete_layout(args.delete):
            print(f"Deleted layout '{args.delete}'")
        else:
            print(f"Failed to delete layout '{args.delete}'")

    elif args.set_workspace:
        workspace_id = int(args.set_workspace[0])
        layout_name = args.set_workspace[1]

        if library.set_active_layout(workspace_id, layout_name):
            print(f"Set workspace {workspace_id} → layout '{layout_name}'")
        else:
            print(f"Failed to set workspace mapping")

    elif args.list_workspaces:
        print("Workspace → Layout mappings:")
        print("-" * 60)

        # Show all mappings
        for ws_id in sorted(library._workspace_mappings.keys()):
            layout_name = library._workspace_mappings[ws_id]
            layout = library.load_layout(layout_name)
            zone_count = len(layout.zones) if layout else 0
            print(f"  Workspace {ws_id} → {layout_name} ({zone_count} zones)")

        if not library._workspace_mappings:
            print("  No workspace mappings configured.")

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
