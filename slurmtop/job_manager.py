"""Job list management for SlurmTop."""

from __future__ import annotations

import os
from typing import Callable, Optional

from textual.widgets import ListView

from slurmtop.models import DEFAULT_CURRENT_JOBS, DEFAULT_PAST_JOBS, Job, fetch_current_jobs, fetch_past_jobs
from slurmtop.widgets import JobListItem


class JobListManager:
    """Manages job lists with automatic refresh from SLURM."""

    def __init__(
        self,
        *,
        use_slurm: bool = True,
        refresh_interval: float = 30.0,
        filter_user: Optional[str] = None,
    ) -> None:
        """
        Initialize the job list manager.
        
        Args:
            use_slurm: Whether to fetch from SLURM or use mock data.
            refresh_interval: Seconds between automatic refreshes (default: 30.0).
            filter_user: Username to filter jobs. If None, uses $USER environment variable.
        """
        self.use_slurm = use_slurm
        self.refresh_interval = refresh_interval
        
        # Use $USER environment variable if no user specified
        self.filter_user = filter_user if filter_user is not None else os.environ.get("USER")
        
        self.current_jobs: list[Job] = []
        self.past_jobs: list[Job] = []
        self.selected_job: Optional[Job] = None
        
        # Callbacks for UI updates
        self._on_jobs_updated: Optional[Callable[[list[Job], list[Job]], None]] = None
        self._on_selection_changed: Optional[Callable[[Optional[Job]], None]] = None
        
        # Initial fetch
        self.refresh()
    
    def set_on_jobs_updated(self, callback: Callable[[list[Job], list[Job]], None]) -> None:
        """Set callback for when jobs are updated."""
        self._on_jobs_updated = callback
    
    def set_on_selection_changed(self, callback: Callable[[Optional[Job]], None]) -> None:
        """Set callback for when selection changes."""
        self._on_selection_changed = callback
    
    def refresh(self) -> None:
        """Refresh job lists from SLURM."""
        if not self.use_slurm:
            self.current_jobs = DEFAULT_CURRENT_JOBS.copy()
            self.past_jobs = DEFAULT_PAST_JOBS.copy()
        else:
            # Fetch from SLURM, using $USER for filtering
            self.current_jobs = fetch_current_jobs(user=self.filter_user)
            self.past_jobs = fetch_past_jobs(user=self.filter_user)
            # Reverse past jobs to have most recent first
            self.past_jobs.reverse()
        
        # Notify listeners
        if self._on_jobs_updated:
            self._on_jobs_updated(self.current_jobs, self.past_jobs)
    
    def _jobs_equal(self, jobs1: list[Job], jobs2: list[Job]) -> bool:
        """Check if two job lists are equal (same jobs with same data)."""
        if len(jobs1) != len(jobs2):
            return False
        for j1, j2 in zip(jobs1, jobs2):
            if not self._job_equal(j1, j2):
                return False
        return True
    
    def _job_equal(self, job1: Job, job2: Job) -> bool:
        """Check if two individual jobs are equal."""
        return (job1.job_id == job2.job_id and 
                job1.name == job2.name and
                job1.state == job2.state and
                job1.runtime == job2.runtime and
                job1.nodes == job2.nodes and
                job1.user == job2.user and
                job1.submitted == job2.submitted and
                job1.reason == job2.reason)
    1
    def update_list_view(
        self,
        list_view: ListView,
        new_jobs: list[Job],
        preserve_selection: bool = True,
    ) -> Optional[Job]:
        """Update a ListView with new job data, minimizing widget churn."""
        # Get current items and jobs
        current_items = [child for child in list_view.children if isinstance(child, JobListItem)]
        current_jobs = [item.job for item in current_items]
        
        # Quick check: if jobs are identical, do nothing
        if self._jobs_equal(current_jobs, new_jobs):
            if preserve_selection and list_view.index is not None and list_view.index < len(current_items):
                return current_items[list_view.index].job
            return None
        
        # Store current selection
        selected_job_id = None
        selected_index = list_view.index
        
        if preserve_selection and selected_index is not None and selected_index < len(current_items):
            selected_job_id = current_items[selected_index].job.job_id
        
        # Build maps for efficient lookup
        current_jobs_map = {job.job_id: (idx, item) for idx, (job, item) in enumerate(zip(current_jobs, current_items))}
        new_jobs_map = {job.job_id: job for job in new_jobs}
        
        # Remove jobs that no longer exist
        for job_id, (idx, item) in reversed(list(current_jobs_map.items())):
            if job_id not in new_jobs_map:
                list_view.remove(item)
        
        # Update existing items and add new ones
        for target_idx, new_job in enumerate(new_jobs):
            if new_job.job_id in current_jobs_map:
                # Job exists - check if it needs updating
                _, existing_item = current_jobs_map[new_job.job_id]
                
                # Update job data if changed (state, runtime, etc.)
                if not self._job_equal(existing_item.job, new_job):
                    existing_item.job = new_job
                    existing_item.refresh_display()
                
                # Check if item is in wrong position
                current_children = list(list_view.children)
                if existing_item in current_children:
                    current_pos = current_children.index(existing_item)
                    if current_pos != target_idx:
                        # Move item to correct position by rebuilding list
                        list_view.remove(existing_item)
                        all_children = list(list_view.children)
                        all_children.insert(target_idx, existing_item)
                        list_view.clear()
                        for child in all_children:
                            list_view.append(child)
            else:
                # New job - add at correct position
                new_item = JobListItem(new_job)
                current_children = list(list_view.children)
                if target_idx >= len(current_children):
                    list_view.append(new_item)
                else:
                    current_children.insert(target_idx, new_item)
                    list_view.clear()
                    for child in current_children:
                        list_view.append(child)
        
        # Try to restore selection by job_id
        if preserve_selection and selected_job_id:
            for idx, child in enumerate(list_view.children):
                if isinstance(child, JobListItem) and child.job.job_id == selected_job_id:
                    list_view.index = idx
                    return child.job
        
        # Fallback: keep same index if possible
        if selected_index is not None and selected_index < len(list_view.children):
            list_view.index = selected_index
            item = list_view.children[selected_index]
            if isinstance(item, JobListItem):
                return item.job
        
        # Default to first item
        if list_view.children:
            list_view.index = 0
            item = list_view.children[0]
            if isinstance(item, JobListItem):
                return item.job
        
        return None
    
    def update_list_view2(
        self,
        list_view: ListView,
        jobs: list[Job],
        preserve_selection: bool = True,
    ) -> Optional[Job]:
        """
        Update a ListView with jobs.
        
        Args:
            list_view: The ListView to update.
            jobs: List of jobs to display.
            preserve_selection: Try to restore previously selected job.
        
        Returns:
            The selected job after update, if any.
        """
        # Extract current jobs from list view
        current_jobs = []
        for item in list_view.children:
            if isinstance(item, JobListItem):
                current_jobs.append(item.job)
        
        # Check if jobs actually changed - if not, don't rebuild
        if self._jobs_equal(current_jobs, jobs):
            # Jobs haven't changed, just return current selection
            if preserve_selection and list_view.index is not None and list_view.children:
                current_item = list_view.children[list_view.index]
                if isinstance(current_item, JobListItem):
                    return current_item.job
            return None
        
        # Remember current selection and scroll position
        selected_job_id = self.selected_job.job_id if self.selected_job and preserve_selection else None
        current_index = list_view.index if list_view.index is not None else 0
        
        # Clear and rebuild only if jobs changed
        list_view.clear()
        for job in jobs:
            list_view.append(JobListItem(job))
        
        # Try to restore selection
        selected = None
        if selected_job_id:
            for idx, item in enumerate(list_view.children):
                if isinstance(item, JobListItem) and item.job.job_id == selected_job_id:
                    list_view.index = idx
                    selected = item.job
                    break
        
        # If selection not restored, try to keep the same index position
        if selected is None and list_view.children:
            # Keep the same index if possible, otherwise default to 0
            if current_index < len(list_view.children):
                list_view.index = current_index
            else:
                list_view.index = 0
            
            item = list_view.children[list_view.index]
            if isinstance(item, JobListItem):
                selected = item.job
        
        return selected
    
    def select_job(self, job: Optional[Job]) -> None:
        """Update the selected job and notify listeners."""
        self.selected_job = job
        if self._on_selection_changed:
            self._on_selection_changed(job)
    
    def get_current_jobs(self) -> list[Job]:
        """Get current job list."""
        return self.current_jobs
    
    def get_past_jobs(self) -> list[Job]:
        """Get past job list."""
        return self.past_jobs
    
    def get_selected_job(self) -> Optional[Job]:
        """Get currently selected job."""
        return self.selected_job
