// api.ts — Todo API client, mirrors the backend endpoints.
import type { Todo, DailyItem, Priority, ISODate } from "./types";

const BASE = "/api/projects";

export async function fetchTodos(project: string): Promise<Todo[]> {
  const res = await fetch(`${BASE}/${project}/todos`);
  if (!res.ok) throw new Error(`Failed to fetch todos: ${res.status}`);
  return res.json();
}

export async function createTodo(project: string, title: string, priority: Priority, labels: string[] = []): Promise<Todo> {
  const res = await fetch(`${BASE}/${project}/todos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, priority, labels }),
  });
  if (!res.ok) throw new Error(`Failed to create todo: ${res.status}`);
  return res.json();
}

export async function patchTodo(project: string, id: string, fields: Record<string, unknown>): Promise<Todo> {
  const res = await fetch(`${BASE}/${project}/todos/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error(`Failed to patch todo: ${res.status}`);
  return res.json();
}

export async function deleteTodo(project: string, id: string): Promise<void> {
  const res = await fetch(`${BASE}/${project}/todos/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to delete todo: ${res.status}`);
}

export interface DailyResponse {
  date: string;
  items: DailyItem[];
}

export async function fetchDaily(project: string, date: ISODate): Promise<DailyResponse> {
  const res = await fetch(`${BASE}/${project}/daily/${date}`);
  if (!res.ok) throw new Error(`Failed to fetch daily: ${res.status}`);
  return res.json();
}

export async function addDailyItem(project: string, date: ISODate, todoId: string, title: string, priority: Priority = "normal"): Promise<DailyItem> {
  const res = await fetch(`${BASE}/${project}/daily/${date}/items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ todo_id: todoId, title, priority }),
  });
  if (!res.ok) throw new Error(`Failed to add daily item: ${res.status}`);
  return res.json();
}

export async function patchDailyItem(project: string, date: ISODate, itemId: string, fields: Record<string, unknown>): Promise<DailyItem> {
  const res = await fetch(`${BASE}/${project}/daily/${date}/items/${itemId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error(`Failed to patch daily item: ${res.status}`);
  return res.json();
}

export async function deleteDailyItem(project: string, date: ISODate, itemId: string): Promise<void> {
  const res = await fetch(`${BASE}/${project}/daily/${date}/items/${itemId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to delete daily item: ${res.status}`);
}

export async function carryOver(project: string, fromDate: ISODate, toDate: ISODate): Promise<DailyResponse> {
  const res = await fetch(`${BASE}/${project}/daily/${toDate}/carry-over`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from_date: fromDate, to_date: toDate }),
  });
  if (!res.ok) throw new Error(`Failed to carry over: ${res.status}`);
  return res.json();
}
