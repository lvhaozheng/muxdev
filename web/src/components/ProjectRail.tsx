import {
  FolderOpen,
  Plus,
  SquaresFour,
  X,
} from "@phosphor-icons/react";
import { useState } from "react";
import type { Project } from "../types";

interface Props {
  projects: Project[];
  selectedId: string | null;
  busy: boolean;
  onSelect: (projectId: string) => void;
  onAdd: (path: string) => Promise<void>;
}

export function ProjectRail({
  projects,
  selectedId,
  busy,
  onSelect,
  onAdd,
}: Props) {
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState("");

  async function submit() {
    if (!path.trim() || busy) return;
    await onAdd(path.trim());
    setPath("");
    setAdding(false);
  }

  return (
    <aside className="project-rail" aria-label="项目工作台">
      <div className="project-logo" title="Muxdev Workbench">
        <SquaresFour weight="fill" aria-hidden="true" />
        <span className="sr-only">Muxdev Workbench</span>
      </div>
      <nav aria-label="已登记项目">
        {projects.map((project) => (
          <button
            type="button"
            key={project.project_id}
            className={`project-button ${
              selectedId === project.project_id ? "selected" : ""
            }`}
            onClick={() => onSelect(project.project_id)}
            aria-current={selectedId === project.project_id ? "page" : undefined}
            aria-label={`${project.name}，${project.needs_you} 项需要处理`}
            title={`${project.name}\n${project.path}`}
          >
            <FolderOpen weight={selectedId === project.project_id ? "fill" : "regular"} />
            <span>{project.name.slice(0, 2).toUpperCase()}</span>
            {project.needs_you ? <small>{project.needs_you}</small> : null}
            <i className={project.available ? "online" : "offline"} aria-hidden="true" />
          </button>
        ))}
      </nav>
      <button
        className="project-add"
        type="button"
        onClick={() => setAdding((value) => !value)}
        aria-expanded={adding}
        aria-label="登记本地项目"
        title="登记本地项目"
      >
        {adding ? <X /> : <Plus weight="bold" />}
      </button>
      {adding ? (
        <form
          className="project-add-popover"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <strong>登记本地项目</strong>
          <p>输入已存在的目录；不会移动或删除项目文件。</p>
          <input
            autoFocus
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="D:\projects\my-app"
            aria-label="项目目录"
          />
          <button className="primary" type="submit" disabled={!path.trim() || busy}>
            {busy ? "正在登记…" : "添加项目"}
          </button>
        </form>
      ) : null}
    </aside>
  );
}
