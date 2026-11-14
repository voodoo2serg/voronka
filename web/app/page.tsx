'use client';

import CrmWorkspace from './crm/Workspace';
import OpsWorkspace from './ops/Workspace';

const portal = process.env.NEXT_PUBLIC_PORTAL || 'crm';

export default portal === 'ops' ? OpsWorkspace : CrmWorkspace;
