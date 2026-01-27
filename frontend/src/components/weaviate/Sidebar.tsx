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
  Collapse,
  useTheme,
  useMediaQuery,
} from '@mui/material';
import {
  Storage,
  Settings,
  Schema,
  Description,
  ExpandLess,
  ExpandMore,
  ChevronLeft,
  ChevronRight,
  Tune,
  CloudSync,
  Dashboard,
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
  children?: NavigationItem[];
}

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
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set(['settings']));
  const [collapsed, setCollapsed] = useState(false);

  const navigationItems: NavigationItem[] = [
    {
      id: 'documents',
      label: 'Documents',
      icon: <Description />,
      path: '/weaviate/documents',
    },
    {
      id: 'dashboard',
      label: 'Dashboard',
      icon: <Dashboard />,
      path: '/weaviate/dashboard',
    },
    {
      id: 'settings',
      label: 'Settings',
      icon: <Settings />,
      children: [
        {
          id: 'embeddings',
          label: 'Embeddings',
          icon: <CloudSync />,
          path: '/weaviate/settings/embeddings',
        },
        {
          id: 'database',
          label: 'Database',
          icon: <Storage />,
          path: '/weaviate/settings/database',
        },
        {
          id: 'schema',
          label: 'Schema',
          icon: <Schema />,
          path: '/weaviate/settings/schema',
        },
        {
          id: 'chunking',
          label: 'Chunking',
          icon: <Tune />,
          path: '/weaviate/settings/chunking',
        },
      ],
    },
  ];

  const handleItemClick = (item: NavigationItem) => {
    if (item.path) {
      navigate(item.path);
      if (isMobile && onToggle) {
        onToggle();
      }
    } else if (item.children) {
      setExpandedItems((prev) => {
        const newSet = new Set(prev);
        if (newSet.has(item.id)) {
          newSet.delete(item.id);
        } else {
          newSet.add(item.id);
        }
        return newSet;
      });
    }
  };

  const toggleCollapse = () => {
    setCollapsed(!collapsed);
  };

  const isActiveRoute = (path?: string): boolean => {
    if (!path) return false;
    // Exact match or starts with path followed by a slash (for sub-routes)
    return location.pathname === path || location.pathname.startsWith(`${path}/`);
  };

  const renderNavigationItem = (item: NavigationItem, depth: number = 0): React.ReactNode => {
    const hasChildren = item.children && item.children.length > 0;
    const isExpanded = expandedItems.has(item.id);
    const isActive = isActiveRoute(item.path);

    return (
      <React.Fragment key={item.id}>
        <ListItem disablePadding sx={{ display: 'block' }}>
          <ListItemButton
            onClick={() => handleItemClick(item)}
            selected={isActive}
            sx={{
              minHeight: 48,
              justifyContent: collapsed ? 'center' : 'initial',
              px: depth === 0 ? 2.5 : 4,
              pl: depth > 0 && !collapsed ? depth * 3 : undefined,
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
              <>
                <ListItemText
                  primary={item.label}
                  primaryTypographyProps={{
                    fontSize: depth === 0 ? '0.95rem' : '0.875rem',
                    fontWeight: isActive ? 600 : 400,
                  }}
                />
                {hasChildren && (isExpanded ? <ExpandLess /> : <ExpandMore />)}
              </>
            )}
          </ListItemButton>
        </ListItem>
        {hasChildren && !collapsed && (
          <Collapse in={isExpanded} timeout="auto" unmountOnExit>
            <List component="div" disablePadding>
              {item.children!.map((child) => renderNavigationItem(child, depth + 1))}
            </List>
          </Collapse>
        )}
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
        <IconButton onClick={toggleCollapse} size="small">
          {collapsed ? <ChevronRight /> : <ChevronLeft />}
        </IconButton>
      </Box>

      <Divider />

      <List sx={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        {navigationItems.map((item) => renderNavigationItem(item))}
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