import { useState, useEffect } from 'react';
import { fetchProject, updateProject } from '../lib/api';
import type { Project, Persona } from '../lib/types';

interface ProjectSettingsProps {
  project: Project;
  personas: Persona[];
  onClose: () => void;
  onUpdated: (project: Project) => void;
  onDeleted?: () => void;
}

export function ProjectSettings({ project, personas, onClose, onUpdated }: ProjectSettingsProps) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description || '');
  const [instructions, setInstructions] = useState(project.instructions || '');
  const [defaultPersona, setDefaultPersona] = useState(project.default_persona || '');
  const [archived, setArchived] = useState(project.archived);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  // Reload fresh data on mount
  useEffect(() => {
    fetchProject(project.id)
      .then(p => {
        setName(p.name);
        setDescription(p.description || '');
        setInstructions(p.instructions || '');
        setDefaultPersona(p.default_persona || '');
        setArchived(p.archived);
      })
      .catch(() => {});
  }, [project.id]);

  const handleSave = async () => {
    if (!name.trim()) { setError('Name is required'); return; }
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const updated = await updateProject(project.id, {
        name: name.trim(),
        description: description.trim() || undefined,
        instructions: instructions.trim() || undefined,
        default_persona: defaultPersona || undefined,
        archived,
      });
      onUpdated(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      setError('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      <div className="max-w-xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <button
            onClick={onClose}
            className="text-text-secondary hover:text-text-primary cursor-pointer"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <h2 className="text-lg font-semibold text-text-primary">Project Settings</h2>
        </div>

        {/* Name */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-text-secondary uppercase tracking-wider">Name</label>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent"
            placeholder="Project name"
          />
        </div>

        {/* Description */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-text-secondary uppercase tracking-wider">Description</label>
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={2}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent resize-none"
            placeholder="Brief description of this project"
          />
        </div>

        {/* Instructions */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-text-secondary uppercase tracking-wider">Instructions</label>
          <p className="text-[11px] text-text-secondary">
            Custom instructions injected into the system prompt for all chats in this project.
          </p>
          <textarea
            value={instructions}
            onChange={e => setInstructions(e.target.value)}
            rows={6}
            maxLength={3000}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-secondary outline-none focus:border-accent resize-none font-mono"
            placeholder="e.g. Always respond in formal academic English. Focus on machine learning topics."
          />
          <div className="text-[11px] text-text-secondary text-right">{instructions.length}/3000</div>
        </div>

        {/* Default persona */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-text-secondary uppercase tracking-wider">Default Persona</label>
          <select
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
          <span className="text-[11px] text-text-secondary">Archived projects are hidden from the sidebar</span>
        </div>

        {/* Error */}
        {error && (
          <div className="text-sm text-error">{error}</div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
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
