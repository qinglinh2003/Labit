// types.ts — shared domain types for the Todo module.

export type Priority = "low" | "normal" | "high";
export type TodoStatus = "open" | "done" | "cancelled";
export type ISODate = string; // "YYYY-MM-DD"

export interface Todo {
  id: string;
  title: string;
  status: TodoStatus;
  priority: Priority;
  labels: string[];
  created_at: string;
}

export interface DailyItem {
  id: string;
  title: string;
  status: "open" | "done";
  /** When set, references a Backlog Todo.id — completing this syncs the Backlog item. */
  todo_id: string;
  priority: Priority;
}
