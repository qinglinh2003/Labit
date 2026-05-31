import { useCallback } from "react";
import { MessageSquarePlus, Trash2 } from "lucide-react";
import type { ChatListItem } from "../api/chat";

export default function ChatList({
  chats,
  activeChatId,
  onSelect,
  onCreate,
  onDelete,
}: {
  chats: ChatListItem[];
  activeChatId: string;
  onSelect: (chatId: string) => void;
  onCreate: () => void;
  onDelete: (chatId: string) => void;
}) {
  const handleDelete = useCallback(
    (e: React.MouseEvent, chatId: string) => {
      e.stopPropagation();
      onDelete(chatId);
    },
    [onDelete],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Chats</span>
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-slate-100"
          onClick={onCreate}
          title="New chat"
        >
          <MessageSquarePlus size={15} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {chats.length === 0 ? (
          <div className="p-3 text-center text-xs text-slate-400">No chats yet</div>
        ) : (
          chats.map((chat) => (
            <button
              key={chat.chat_id}
              type="button"
              className={`group flex w-full items-center gap-2 border-b border-slate-100 px-3 py-2.5 text-left ${
                activeChatId === chat.chat_id ? "bg-slate-100" : "hover:bg-slate-50"
              }`}
              onClick={() => onSelect(chat.chat_id)}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{chat.title}</div>
                <div className="mt-0.5 text-xs text-slate-400">
                  {chat.mode} · {chat.message_count} msgs
                </div>
              </div>
              <button
                type="button"
                className="hidden h-6 w-6 flex-shrink-0 items-center justify-center rounded hover:bg-slate-200 group-hover:flex"
                onClick={(e) => handleDelete(e, chat.chat_id)}
                title="Delete chat"
              >
                <Trash2 size={13} />
              </button>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
