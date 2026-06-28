import React, { useState } from 'react';
import {
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  IconButton,
  Divider,
  Box,
  Typography,
  Tooltip,
  useTheme,
  useMediaQuery,
} from '@mui/material';
import {
  Storage,
  Description,
  ChevronLeft,
  ChevronRight,
  PostAdd,
} from '@mui/icons-material';
import { useNavigate, useLocation } from 'react-router-dom';

interface SidebarProps {
  open?: boolean;
  onToggle?: () => void;
  variant?: 'permanent' | 'persistent' | 'temporary';
  width?: number;
}

interface NavigationItem {
  id: string;
  label: string;
  icon: React.ReactNode;
  path?: string;
  aliases?: string[];
}

const NAVIGATION_ITEMS: NavigationItem[] = [
  {
    id: 'documents',
    label: 'Library',
    icon: <Description />,
    path: '/weaviate/documents',
  },
  {
    id: 'add-literature',
    label: 'Add Literature',
    icon: <PostAdd />,
    path: '/weaviate/add-literature',
    aliases: ['/weaviate/documents/import-mock'],
  },
];

const Sidebar: React.FC<SidebarProps> = ({
  open = true,
  onToggle,
  variant = 'persistent',
  width = 240,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const [collapsed, setCollapsed] = useState(false);

  const handleItemClick = (item: NavigationItem) => {
    if (item.path) {
      navigate(item.path);
      if (isMobile && onToggle) {
        onToggle();
      }
    }
  };

  const toggleCollapse = () => {
    setCollapsed((isCollapsed) => !isCollapsed);
  };

  const isActiveRoute = (path?: string, aliases: string[] = []): boolean => {
    if (!path) return false;
    const normalizedPathname = location.pathname.replace(/\/+$/, '');
    if (aliases.includes(normalizedPathname)) {
      return true;
    }
    if (path === '/weaviate/documents' && normalizedPathname === '/weaviate/documents/import-mock') {
      return false;
    }
    // Exact match or starts with path followed by a slash (for sub-routes)
    return normalizedPathname === path || normalizedPathname.startsWith(`${path}/`);
  };

  const renderNavigationItem = (item: NavigationItem): React.ReactNode => {
    const isActive = isActiveRoute(item.path, item.aliases);

    return (
      <React.Fragment key={item.id}>
        <ListItem disablePadding sx={{ display: 'block' }}>
          <Tooltip title={collapsed ? item.label : ''} placement="right">
            <ListItemButton
              onClick={() => handleItemClick(item)}
              selected={isActive}
              aria-label={collapsed ? item.label : undefined}
              sx={{
                minHeight: 48,
                justifyContent: collapsed ? 'center' : 'initial',
                px: 2.5,
                bgcolor: isActive ? 'action.selected' : 'transparent',
                '&:hover': {
                  bgcolor: 'action.hover',
                },
                '&.Mui-selected': {
                  bgcolor: 'action.selected',
                  borderLeft: `3px solid ${theme.palette.primary.main}`,
                  '&:hover': {
                    bgcolor: 'action.selected',
                  },
                },
              }}
            >
              <ListItemIcon
                sx={{
                  minWidth: 0,
                  mr: collapsed ? 0 : 3,
                  justifyContent: 'center',
                  color: isActive ? 'primary.main' : 'inherit',
                }}
              >
                {item.icon}
              </ListItemIcon>
              {!collapsed && (
                <ListItemText
                  primary={item.label}
                  primaryTypographyProps={{
                    fontSize: '0.95rem',
                    fontWeight: isActive ? 600 : 400,
                  }}
                />
              )}
            </ListItemButton>
          </Tooltip>
        </ListItem>
      </React.Fragment>
    );
  };

  const drawerContent = (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', mt: 8 }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
          p: 2,
          minHeight: 64,
        }}
      >
        {!collapsed && (
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Storage sx={{ mr: 1, color: 'primary.main' }} />
            <Typography variant="h6" noWrap>
              Documents
            </Typography>
          </Box>
        )}
        <IconButton
          onClick={toggleCollapse}
          size="small"
          aria-label={collapsed ? 'Expand Documents navigation' : 'Collapse Documents navigation'}
        >
          {collapsed ? <ChevronRight /> : <ChevronLeft />}
        </IconButton>
      </Box>

      <Divider />

      <List sx={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        {NAVIGATION_ITEMS.map((item) => renderNavigationItem(item))}
      </List>

      <Divider />

      <Box sx={{ p: 2 }}>
        {!collapsed && (
          <Typography variant="caption" color="text.secondary">
            © 2025 AI Curation System
          </Typography>
        )}
      </Box>
    </Box>
  );

  const actualVariant = isMobile ? 'temporary' : variant;
  const actualWidth = collapsed ? 72 : width;

  return (
    <Drawer
      variant={actualVariant}
      open={open}
      onClose={onToggle}
      sx={{
        width: actualWidth,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: actualWidth,
          boxSizing: 'border-box',
          transition: theme.transitions.create('width', {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.enteringScreen,
          }),
          overflowX: 'hidden',
        },
      }}
      anchor="left"
    >
      {drawerContent}
    </Drawer>
  );
};

export default Sidebar;
