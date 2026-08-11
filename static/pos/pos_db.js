/**
 * North POS - IndexedDB Client Engine & Offline Sync Manager
 * Incluye gestión del catálogo, tickets, turnos y promociones (Capítulo 7)
 */
class PosDB {
    constructor() {
        this.dbName = 'NorthPOS_DB';
        this.dbVersion = 2; // Incrementado para agregar promociones store
        this.db = null;
    }

    async init() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.dbVersion);

            request.onupgradeneeded = (event) => {
                const db = event.target.result;

                // Catalog Store
                if (!db.objectStoreNames.contains('catalog')) {
                    const catalogStore = db.createObjectStore('catalog', { keyPath: 'id_articulo' });
                    catalogStore.createIndex('referencia', 'referencia', { unique: false });
                    catalogStore.createIndex('codigo_barras', 'codigo_barras', { unique: false });
                    catalogStore.createIndex('nom_articulo', 'nom_articulo', { unique: false });
                }

                // Terceros Store
                if (!db.objectStoreNames.contains('terceros')) {
                    const tercerosStore = db.createObjectStore('terceros', { keyPath: 'id_tercero' });
                    tercerosStore.createIndex('nom_tercero', 'nom_tercero', { unique: false });
                }

                // Tickets Store
                if (!db.objectStoreNames.contains('tickets')) {
                    const ticketStore = db.createObjectStore('tickets', { keyPath: 'ticket_id' });
                    ticketStore.createIndex('sync_status', 'sync_status', { unique: false });
                    ticketStore.createIndex('fch_timestamp', 'fch_timestamp', { unique: false });
                }

                // Shift Info Store
                if (!db.objectStoreNames.contains('shift')) {
                    db.createObjectStore('shift', { keyPath: 'key' });
                }

                // Promociones Store (Capítulo 7)
                if (!db.objectStoreNames.contains('promociones')) {
                    db.createObjectStore('promociones', { keyPath: 'id_promocion' });
                }
            };

            request.onsuccess = (event) => {
                this.db = event.target.result;
                console.log('[PosDB] IndexedDB inicializada correctamente (v2).');
                this.runGarbageCollection();
                resolve(this.db);
            };

            request.onerror = (event) => {
                console.error('[PosDB] Error al abrir IndexedDB:', event.target.error);
                reject(event.target.error);
            };
        });
    }

    // --- CATÁLOGO ---
    async saveCatalog(items) {
        if (!this.db) await this.init();
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction('catalog', 'readwrite');
            const store = tx.objectStore('catalog');
            store.clear();

            items.forEach(item => {
                store.put(item);
            });

            tx.oncomplete = () => {
                console.log(`[PosDB] Catálogo cargado en IndexedDB (${items.length} artículos).`);
                resolve(true);
            };
            tx.onerror = (e) => reject(e.target.error);
        });
    }

    async getCatalog() {
        if (!this.db) await this.init();
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction('catalog', 'readonly');
            const store = tx.objectStore('catalog');
            const req = store.getAll();
            req.onsuccess = () => resolve(req.result || []);
            req.onerror = (e) => reject(e.target.error);
        });
    }

    // --- PROMOCIONES (CAPÍTULO 7) ---
    async savePromociones(promos) {
        if (!this.db) await this.init();
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction('promociones', 'readwrite');
            const store = tx.objectStore('promociones');
            store.clear();

            promos.forEach(p => {
                store.put(p);
            });

            tx.oncomplete = () => {
                console.log(`[PosDB] ${promos.length} Reglas de promociones guardadas en IndexedDB.`);
                resolve(true);
            };
            tx.onerror = (e) => reject(e.target.error);
        });
    }

    async getPromociones() {
        if (!this.db) await this.init();
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction('promociones', 'readonly');
            const store = tx.objectStore('promociones');
            const req = store.getAll();
            req.onsuccess = () => resolve(req.result || []);
            req.onerror = (e) => reject(e.target.error);
        });
    }

    // --- TICKETS OFFLINE ---
    async saveTicket(ticket) {
        if (!this.db) await this.init();
        ticket.sync_status = false;
        ticket.fch_timestamp = Date.now();

        return new Promise((resolve, reject) => {
            const tx = this.db.transaction('tickets', 'readwrite');
            const store = tx.objectStore('tickets');
            store.put(ticket);

            tx.oncomplete = () => {
                console.log(`[PosDB] Ticket ${ticket.ticket_id} guardado localmente (sync_status=false).`);
                resolve(ticket);
            };
            tx.onerror = (e) => reject(e.target.error);
        });
    }

    async getUnsyncedTickets() {
        if (!this.db) await this.init();
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction('tickets', 'readonly');
            const store = tx.objectStore('tickets');
            const req = store.getAll();
            req.onsuccess = () => {
                const all = req.result || [];
                const unsynced = all.filter(t => t.sync_status === false);
                resolve(unsynced);
            };
            req.onerror = (e) => reject(e.target.error);
        });
    }

    async markTicketsSynced(ticketIds) {
        if (!this.db) await this.init();
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction('tickets', 'readwrite');
            const store = tx.objectStore('tickets');

            ticketIds.forEach(id => {
                const req = store.get(id);
                req.onsuccess = () => {
                    if (req.result) {
                        const updated = req.result;
                        updated.sync_status = true;
                        store.put(updated);
                    }
                };
            });

            tx.oncomplete = () => resolve(true);
            tx.onerror = (e) => reject(e.target.error);
        });
    }

    // --- GARBAGE COLLECTION (PURGA LOCAL 3 DÍAS) ---
    async runGarbageCollection() {
        if (!this.db) return;
        const THREE_DAYS_MS = 3 * 24 * 60 * 60 * 1000;
        const now = Date.now();
        const cutoff = now - THREE_DAYS_MS;

        const tx = this.db.transaction('tickets', 'readwrite');
        const store = tx.objectStore('tickets');
        const req = store.getAll();

        req.onsuccess = () => {
            const tickets = req.result || [];
            let purgedCount = 0;
            tickets.forEach(ticket => {
                if (ticket.sync_status === true && ticket.fch_timestamp < cutoff) {
                    store.delete(ticket.ticket_id);
                    purgedCount++;
                }
            });
            if (purgedCount > 0) {
                console.log(`[PosDB] Garbage Collection ejecutado: ${purgedCount} tickets antiguos purgados.`);
            }
        };
    }
}

window.posDB = new PosDB();
