// useTodos.ts — React Query state + optimistic mutations for the Todo module.
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { Todo, DailyItem, Priority, ISODate } from "./types";
import * as api from "./api";

function getToday(): ISODate {
  return new Date().toISOString().slice(0, 10);
}

export function shiftISO(iso: ISODate, days: number): ISODate {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

export function dateLabel(iso: ISODate): string {
  const today = getToday();
  if (iso === today) return "Today";
  if (iso === shiftISO(today, -1)) return "Yesterday";
  if (iso === shiftISO(today, 1)) return "Tomorrow";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });
}

export function useTodos(project: string, onToast?: (msg: string) => void) {
  const qc = useQueryClient();
  const [date, setDate] = useState<ISODate>(getToday());

  const todosKey = ["todos", project] as const;
  const dailyKey = ["daily", project, date] as const;
  const prevDailyKey = ["daily", project, shiftISO(date, -1)] as const;

  const todosQuery = useQuery({
    queryKey: todosKey,
    queryFn: () => api.fetchTodos(project),
    enabled: !!project,
  });

  const dailyQuery = useQuery({
    queryKey: dailyKey,
    queryFn: () => api.fetchDaily(project, date),
    enabled: !!project,
  });

  // Prefetch previous day for carry-over count
  const prevDailyQuery = useQuery({
    queryKey: prevDailyKey,
    queryFn: () => api.fetchDaily(project, shiftISO(date, -1)),
    enabled: !!project,
  });

  const backlog = todosQuery.data ?? [];
  const dayItems = dailyQuery.data?.items ?? [];

  const linkedIds = useMemo(() => new Set(dayItems.filter((i) => i.todo_id).map((i) => i.todo_id)), [dayItems]);

  const prevUnfinished = prevDailyQuery.data?.items.filter((i) => i.status !== "done") ?? [];
  const carryCount = useMemo(() => {
    const have = new Set(dayItems.map((i) => i.todo_id || `t:${i.title}`));
    return prevUnfinished.filter((i) => !have.has(i.todo_id || `t:${i.title}`)).length;
  }, [prevUnfinished, dayItems]);

  // ---- Backlog mutations ----
  const addBacklogMut = useMutation({
    mutationFn: ({ title, priority }: { title: string; priority: Priority }) =>
      api.createTodo(project, title, priority),
    onSuccess: () => qc.invalidateQueries({ queryKey: todosKey }),
  });

  const deleteBacklogMut = useMutation({
    mutationFn: (id: string) => api.deleteTodo(project, id),
    onMutate: (id) => {
      qc.setQueryData<Todo[]>(todosKey, (old) => old?.filter((x) => x.id !== id));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: todosKey }),
  });

  const cyclePriMut = useMutation({
    mutationFn: ({ id, priority }: { id: string; priority: Priority }) =>
      api.patchTodo(project, id, { priority }),
    onMutate: ({ id, priority }) => {
      qc.setQueryData<Todo[]>(todosKey, (old) => old?.map((x) => x.id === id ? { ...x, priority } : x));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: todosKey }),
  });

  const toggleBacklogDoneMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patchTodo(project, id, { status }),
    onMutate: ({ id, status }) => {
      qc.setQueryData<Todo[]>(todosKey, (old) => old?.map((x) => x.id === id ? { ...x, status: status as Todo["status"] } : x));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: todosKey }),
  });

  // ---- Daily mutations ----
  const addDailyMut = useMutation({
    mutationFn: (title: string) => api.addDailyItem(project, date, "", title),
    onSuccess: () => qc.invalidateQueries({ queryKey: dailyKey }),
  });

  const addFromBacklogMut = useMutation({
    mutationFn: (item: Todo) => api.addDailyItem(project, date, item.id, item.title, item.priority),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: dailyKey });
      onToast?.(`Added to ${date === getToday() ? "today" : dateLabel(date)}`);
    },
  });

  const toggleDailyMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patchDailyItem(project, date, id, { status }),
    onMutate: ({ id, status }) => {
      qc.setQueryData<api.DailyResponse>(dailyKey, (old) => {
        if (!old) return old;
        const items = old.items.map((i) => i.id === id ? { ...i, status: status as DailyItem["status"] } : i);
        const changed = items.find((i) => i.id === id);
        // Optimistically sync linked backlog item
        if (changed?.todo_id) {
          const bkStatus = status === "done" ? "done" : "open";
          qc.setQueryData<Todo[]>(todosKey, (old) => old?.map((x) => x.id === changed.todo_id ? { ...x, status: bkStatus as Todo["status"] } : x));
          if (status === "done") onToast?.("Synced to backlog \u2713");
        }
        return { ...old, items };
      });
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: dailyKey });
      qc.invalidateQueries({ queryKey: todosKey });
    },
  });

  const deleteDailyMut = useMutation({
    mutationFn: (id: string) => api.deleteDailyItem(project, date, id),
    onMutate: (id) => {
      qc.setQueryData<api.DailyResponse>(dailyKey, (old) => {
        if (!old) return old;
        return { ...old, items: old.items.filter((i) => i.id !== id) };
      });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: dailyKey }),
  });

  const cycleDailyPriMut = useMutation({
    mutationFn: ({ id, priority }: { id: string; priority: Priority }) =>
      api.patchDailyItem(project, date, id, { priority }),
    onMutate: ({ id, priority }) => {
      qc.setQueryData<api.DailyResponse>(dailyKey, (old) => {
        if (!old) return old;
        return { ...old, items: old.items.map((i) => i.id === id ? { ...i, priority } : i) };
      });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: dailyKey }),
  });

  const carryOverMut = useMutation({
    mutationFn: () => api.carryOver(project, shiftISO(date, -1), date),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: dailyKey });
      onToast?.(`Carried over`);
    },
  });

  return {
    backlog,
    dayItems,
    date,
    linkedIds,
    carryCount,
    isLoading: todosQuery.isLoading || dailyQuery.isLoading,
    prevDay: () => setDate((x) => shiftISO(x, -1)),
    nextDay: () => setDate((x) => shiftISO(x, 1)),
    addBacklog: (title: string, priority: Priority) => addBacklogMut.mutate({ title, priority }),
    deleteBacklog: (id: string) => deleteBacklogMut.mutate(id),
    cyclePri: (id: string, p: Priority) => cyclePriMut.mutate({ id, priority: p }),
    toggleBacklogDone: (id: string) => {
      const item = backlog.find((x) => x.id === id);
      if (!item) return;
      toggleBacklogDoneMut.mutate({ id, status: item.status === "done" ? "open" : "done" });
    },
    addDaily: (title: string) => addDailyMut.mutate(title),
    addFromBacklog: (item: Todo) => {
      if (dayItems.some((i) => i.todo_id === item.id)) { onToast?.("Already on this day"); return; }
      addFromBacklogMut.mutate(item);
    },
    dropBacklog: (id: string) => {
      const item = backlog.find((x) => x.id === id);
      if (!item) return;
      if (dayItems.some((i) => i.todo_id === item.id)) { onToast?.("Already on this day"); return; }
      addFromBacklogMut.mutate(item);
    },
    toggleDaily: (id: string) => {
      const item = dayItems.find((i) => i.id === id);
      if (!item) return;
      toggleDailyMut.mutate({ id, status: item.status === "done" ? "open" : "done" });
    },
    deleteDaily: (id: string) => deleteDailyMut.mutate(id),
    cycleDailyPri: (id: string, p: Priority) => cycleDailyPriMut.mutate({ id, priority: p }),
    carryOver: () => carryOverMut.mutate(),
  };
}
