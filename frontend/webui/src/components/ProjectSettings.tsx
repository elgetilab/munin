import { useId, useState, useEffect, useRef } from 'react';
import { fetchProject, updateProject } from '../lib/api';
import type { Project, Persona } from '../lib/types';
import { ErrorNotice } from './ui/ErrorNotice';

// What the backend accepts (backend/retrieval/project_store.py).
const NAME_LIMIT = 200;
const DESCRIPTION_LIMIT = 1000;
const INSTRUCTIONS_LIMIT = 2000;

interface ProjectSettingsProps {
  project: Project;
  personas: Persona[];
  onClose: () => void;
  onUpdated: (project: Project) => void;
  onDeleted?: () => void;
}

// The key gives each project its own form state, so opening another project
// while this page is showing can't leave the old project's values under the new
// one, where Save would write them.
export function ProjectSettings(props: ProjectSettingsProps) {
  return <ProjectSettingsForm key={props.project.id} {...props} />;
}

function ProjectSettingsForm({ project, personas, onClose, onUpdated }: ProjectSettingsProps) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description || '');
  const [instructions, setInstructions] = useState(project.instructions || '');
  const [defaultPersona, setDefaultPersona] = useState(project.default_persona || '');
  const [archived, setArchived] = useState(project.archived);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [loadFailed, setLoadFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const edited = useRef(false);
  const fieldId = useId();

  // Reload fresh data on mount. A response that arrives after the user started
  // typing leaves their input alone.
  useEffect(() => {
    let cancelled = false;
    fetchProject(project.id)
      .then(p => {
        if (cancelled) return;
        setLoadFailed(false);
        if (edited.current) return;
        setName(p.name);
        setDescription(p.description || '');
        setInstructions(p.instructions || '');
        setDefaultPersona(p.default_persona || '');
        setArchived(p.archived);
      })
      .catch(() => { if (!cancelled) setLoadFailed(true); });
    return () => { cancelled = true; };
  }, [project.id, reloadKey]);

  const retryLoad = () => {
    setLoadFailed(false);
    setReloadKey(k => k + 1);
  };

  const handleSave = async () => {
    if (!name.trim()) { setError('Name is required'); return; }
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const updated = await updateProject(project.id, {
        name: name.trim(),
        description: description.trim() || null,
        instructions: instructions.trim() || null,
        default_persona: defaultPersona || null,
        archived,
      });
      onUpdated(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      <div className="max-w-xl mx-auto space-y-6" onChange={() => { edited.current = true; }}>
        {/* Header */}
        <div className="flex items-center gap-3">
          <button
            onClick={onClose}
            aria-label="Back to chat"
            className="text-text-secondary hover:text-text-primary cursor-pointer"
          >
            <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <h2 className="text-lg font-semibold text-text-primary">Project Settings</h2>
        </div>

        {loadFailed && (
          <ErrorNotice
            message="Could not load this project's current settings. Saving is off until they load, so older values can't overwrite newer ones."
            onRetry={retryLoad}
          />
        )}

        {/* Name */}
        <div className="space-y-1.5">
          <label htmlFor={`${fieldId}-name`} className="text-xs font-medium text-text-secondary uppercase tracking-wider">Name</label>
          <input
            id={`${fieldId}-name`}
            value={name}
            onChange={e => setName(e.target.value)}
            maxLength={NAME_LIMIT}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent"
            placeholder="Project name"
          />
        </div>

        {/* Description */}
        <div className="space-y-1.5">
          <label htmlFor={`${fieldId}-description`} className="text-xs font-medium text-text-secondary uppercase tracking-wider">Description</label>
          <textarea
            id={`${fieldId}-description`}
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={2}
            maxLength={DESCRIPTION_LIMIT}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent resize-none"
            placeholder="Brief description of this project"
          />
          <div className="text-[11px] text-text-secondary text-right">{description.length}/{DESCRIPTION_LIMIT}</div>
        </div>

        {/* Instructions */}
        <div className="space-y-1.5">
          <label htmlFor={`${fieldId}-instructions`} className="text-xs font-medium text-text-secondary uppercase tracking-wider">Instructions</label>
          <p className="text-[11px] text-text-secondary">
            Custom instructions injected into the system prompt for all chats in this project.
          </p>
          <textarea
            id={`${fieldId}-instructions`}
            value={instructions}
            onChange={e => setInstructions(e.target.value)}
            rows={6}
            maxLength={INSTRUCTIONS_LIMIT}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent resize-none font-mono"
            placeholder="e.g. Always respond in formal academic English. Focus on machine learning topics."
          />
          <div className="text-[11px] text-text-secondary text-right">{instructions.length}/{INSTRUCTIONS_LIMIT}</div>
        </div>

        {/* Default persona */}
        <div className="space-y-1.5">
          <label htmlFor={`${fieldId}-persona`} className="text-xs font-medium text-text-secondary uppercase tracking-wider">Default Persona</label>
          <select
            id={`${fieldId}-persona`}
            value={defaultPersona}
            onChange={e => setDefaultPersona(e.target.value)}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary outline-none focus:border-accent cursor-pointer"
          >
            <option value="">Use global default</option>
            {personas.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>

        {/* Archived toggle */}
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={archived}
              onChange={e => setArchived(e.target.checked)}
              className="accent-accent"
            />
            <span className="text-sm text-text-primary">Archived</span>
          </label>
          <span className="text-[11px] text-text-secondary">Archived projects move to the Archived group in the sidebar, where you can restore them</span>
        </div>

        {/* Error */}
        {error && (
          <div className="text-sm text-error">{error}</div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving || loadFailed}
            className="px-4 py-2 bg-accent text-bg-primary rounded-lg text-sm font-medium cursor-pointer hover:bg-accent-hover disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving...' : saved ? 'Saved!' : 'Save'}
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-bg-tertiary text-text-secondary rounded-lg text-sm cursor-pointer hover:text-text-primary transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
