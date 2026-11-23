import React, { useState, useEffect, useRef, useMemo } from 'react';
import { taskService } from '../../tasks/services/taskService';
import { TASK_STATUS_LABEL } from '../../tasks/constants/taskEnums';

/**
 * RelatedTaskSelect — Searchable select for associating a ticket with a project task.
 * - Lists only tasks of the chosen project.
 * - Searchable by TASK key and title.
 * - Shows TASK key, title, and status.
 * - Placeholder: "No related task" (empty by default).
 * - Changing the project clears an incompatible task.
 */
export const RelatedTaskSelect = ({
  projectId,
  selectedTaskId,
  onChange,
  disabled = false,
  className = '',
}) => {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef(null);

  // Fetch tasks when projectId changes
  useEffect(() => {
    if (!projectId) {
      setTasks([]);
      if (selectedTaskId) {
        onChange?.('', null);
      }
      return;
    }

    let isMounted = true;
    setLoading(true);

    taskService.list({ project_id: projectId, page_size: 100 })
      .then((res) => {
        if (!isMounted) return;
        const items = res?.items || (Array.isArray(res) ? res : []);
        setTasks(items);

        // Incompatible task check: if current selectedTaskId not in this project, clear it
        if (selectedTaskId) {
          const matching = items.find((t) => t.id === selectedTaskId);
          if (!matching) {
            onChange?.('', null);
          }
        }
      })
      .catch((err) => {
        console.warn('Failed to load tasks for project:', err);
        if (isMounted) setTasks([]);
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [projectId]);

  // Close dropdown on outside click or escape
  useEffect(() => {
    const handleOutside = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setIsOpen(false);
      }
    };
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') setIsOpen(false);
    };
    document.addEventListener('mousedown', handleOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  const selectedTask = useMemo(() => {
    if (!selectedTaskId) return null;
    return tasks.find((t) => t.id === selectedTaskId) || null;
  }, [selectedTaskId, tasks]);

  const filteredTasks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tasks;
    return tasks.filter((t) => {
      const key = (t.task_number || t.key || '').toLowerCase();
      const title = (t.title || '').toLowerCase();
      return key.includes(q) || title.includes(q);
    });
  }, [tasks, search]);

  const handleSelect = (task) => {
    if (!task) {
      onChange?.('', null);
    } else {
      onChange?.(task.id, task);
    }
    setIsOpen(false);
    setSearch('');
  };

  const getStatusColor = (status) => {
    const s = (status || '').toLowerCase();
    if (s === 'done' || s === 'resolved') return 'bg-status-resolved/10 text-status-resolved border-status-resolved/30';
    if (s === 'in_progress' || s === 'wip') return 'bg-status-wip/10 text-status-wip border-status-wip/30';
    if (s === 'review') return 'bg-status-open/10 text-status-open border-status-open/30';
    return 'bg-canvas-lavender text-text-secondary border-border-subtle';
  };

  return (
    <div className={`relative ${className}`} ref={containerRef}>
      {/* Trigger Button */}
      <button
        type="button"
        disabled={disabled || !projectId}
        onClick={() => setIsOpen((prev) => !prev)}
        className={`w-full min-h-[40px] px-3 py-2 rounded-lg border text-left flex items-center justify-between gap-2 text-body-sm transition-all ${
          !projectId
            ? 'bg-canvas-main/50 border-border-subtle/50 text-text-muted cursor-not-allowed'
            : isOpen
            ? 'bg-surface-card border-primary ring-2 ring-primary/20 text-text-primary'
            : 'bg-canvas-main hover:bg-surface-card border-border-subtle text-text-primary shadow-xs'
        }`}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="material-symbols-outlined text-[18px] text-text-muted shrink-0">
            {selectedTask ? 'task_alt' : 'assignment'}
          </span>
          {selectedTask ? (
            <div className="flex items-center gap-2 min-w-0 flex-1 truncate">
              <span className="font-mono font-bold text-xs text-primary bg-primary-fixed/40 px-1.5 py-0.5 rounded">
                {selectedTask.task_number || selectedTask.key}
              </span>
              <span className="font-medium truncate text-text-primary">
                {selectedTask.title}
              </span>
              <span className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border shrink-0 ${getStatusColor(selectedTask.status)}`}>
                {TASK_STATUS_LABEL[selectedTask.status] || selectedTask.status}
              </span>
            </div>
          ) : (
            <span className="text-text-muted">
              {!projectId ? 'Select a project first' : 'No related task'}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {selectedTask && (
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation();
                handleSelect(null);
              }}
              title="Clear related task"
              className="w-5 h-5 rounded-full hover:bg-canvas-lavender flex items-center justify-center text-text-muted hover:text-text-primary transition-colors"
            >
              <span className="material-symbols-outlined text-[14px]">close</span>
            </span>
          )}
          <span className="material-symbols-outlined text-[18px] text-text-muted">
            {isOpen ? 'expand_less' : 'expand_more'}
          </span>
        </div>
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div className="absolute left-0 right-0 top-full mt-1.5 z-50 bg-surface-card rounded-xl shadow-xl border border-border-subtle overflow-hidden flex flex-col max-h-72 animate-in fade-in zoom-in-95 duration-150">
          {/* Search Header */}
          <div className="p-2 border-b border-border-subtle bg-canvas-main/40">
            <div className="relative flex items-center">
              <span className="material-symbols-outlined absolute left-2.5 text-text-muted text-[16px]">
                search
              </span>
              <input
                type="text"
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search tasks by key or title..."
                className="w-full h-8 pl-8 pr-3 rounded-md bg-canvas-main border border-border-subtle text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary"
              />
            </div>
          </div>

          {/* Options List */}
          <div className="flex-1 overflow-y-auto p-1 divide-y divide-border-subtle/30">
            {/* Option: Clear / No Related Task */}
            <button
              type="button"
              onClick={() => handleSelect(null)}
              className={`w-full px-3 py-2 text-left rounded-lg text-xs flex items-center justify-between gap-2 transition-colors ${
                !selectedTaskId
                  ? 'bg-primary-fixed/30 text-primary font-semibold'
                  : 'hover:bg-canvas-lavender text-text-secondary'
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-[16px] text-text-muted">block</span>
                <span>No related task</span>
              </div>
              {!selectedTaskId && (
                <span className="material-symbols-outlined text-[16px] text-primary">check</span>
              )}
            </button>

            {loading ? (
              <div className="py-6 flex items-center justify-center gap-2 text-text-muted text-xs">
                <span className="material-symbols-outlined text-sm animate-spin">refresh</span>
                <span>Loading project tasks...</span>
              </div>
            ) : filteredTasks.length === 0 ? (
              <div className="py-6 text-center text-text-muted text-xs">
                {tasks.length === 0
                  ? 'No tasks in this project yet.'
                  : 'No tasks matching your search.'}
              </div>
            ) : (
              filteredTasks.map((t) => {
                const isSelected = t.id === selectedTaskId;
                const statusLabel = TASK_STATUS_LABEL[t.status] || t.status;
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => handleSelect(t)}
                    className={`w-full px-3 py-2 text-left rounded-lg text-xs flex items-center justify-between gap-2 transition-colors ${
                      isSelected
                        ? 'bg-primary-fixed/30 text-primary font-semibold'
                        : 'hover:bg-canvas-lavender text-text-primary'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className="font-mono font-bold text-[11px] text-primary shrink-0">
                        {t.task_number || t.key}
                      </span>
                      <span className="truncate">{t.title}</span>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <span className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${getStatusColor(t.status)}`}>
                        {statusLabel}
                      </span>
                      {isSelected && (
                        <span className="material-symbols-outlined text-[16px] text-primary">check</span>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default RelatedTaskSelect;
