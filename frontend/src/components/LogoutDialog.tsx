import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
} from '@mui/material';

interface LogoutDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

/**
 * LogoutDialog: Confirmation dialog for logout action
 *
 * Displays a Material-UI dialog asking the user to confirm they want to log out.
 * This prevents accidental logouts (FR-008).
 *
 * Props:
 * - open: Controls dialog visibility
 * - onClose: Called when user cancels (clicks Cancel or clicks outside dialog)
 * - onConfirm: Called when user confirms logout
 *
 * Design:
 * - Uses MUI Dialog component for consistent styling
 * - Two action buttons: Cancel (neutral) and Log Out (primary)
 * - Clear message: "Are you sure you want to log out?"
 *
 * Usage:
 * ```tsx
 * const [logoutDialogOpen, setLogoutDialogOpen] = useState(false);
 * const { logout } = useAuth();
 *
 * const handleLogoutConfirm = async () => {
 *   setLogoutDialogOpen(false);
 *   await logout();
 * };
 *
 * <LogoutDialog
 *   open={logoutDialogOpen}
 *   onClose={() => setLogoutDialogOpen(false)}
 *   onConfirm={handleLogoutConfirm}
 * />
 * ```
 */
const LogoutDialog: React.FC<LogoutDialogProps> = ({ open, onClose, onConfirm }) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="logout-dialog-title"
      aria-describedby="logout-dialog-description"
    >
      <DialogTitle id="logout-dialog-title">
        Confirm Logout
      </DialogTitle>
      <DialogContent>
        <DialogContentText id="logout-dialog-description">
          Are you sure you want to log out?
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button onClick={onConfirm} color="primary" variant="contained" autoFocus>
          Log Out
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default LogoutDialog;
