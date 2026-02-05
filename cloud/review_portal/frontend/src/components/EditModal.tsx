import { AnimatePresence, motion } from "framer-motion";

interface EditModalProps {
  isOpen: boolean;
  text: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onClose: () => void;
}

export function EditModal({ isOpen, text, onChange, onSave, onClose }: EditModalProps) {
  return (
    <AnimatePresence>
      {isOpen ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-6"
        >
          <motion.div
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 20, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="w-full max-w-2xl bg-review-card border border-review-border rounded-2xl p-6 space-y-4"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Edit translation</h2>
              <button onClick={onClose} className="text-gray-400 hover:text-white">
                Close
              </button>
            </div>
            <textarea
              value={text}
              onChange={(event) => onChange(event.target.value)}
              className="w-full min-h-[160px] bg-black/40 border border-review-border rounded-xl p-4 text-white focus:outline-none focus:ring-2 focus:ring-review-accent"
            />
            <div className="flex items-center justify-end gap-3">
              <button
                onClick={onClose}
                className="px-4 py-2 text-gray-300 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={onSave}
                className="px-5 py-2 bg-review-accent text-black font-semibold rounded-lg"
              >
                Save change
              </button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
